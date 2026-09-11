import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from browser_use import Agent
from browser_use.agent.views import ActionResult
from browser_use.tools.service import Tools

from qwen3_chat_ollama import Qwen3ChatOllama

OLLAMA_HOST = "http://127.0.0.1:11434"

RUNS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "e2e_runs",
)

DEFAULT_TASK = (
    "Open https://example.com, read the page, "
    "and report the page title and URL. Then stop."
)
DEFAULT_EXPECTED = "Example Domain"


_ATTRIBUTION_LOG = os.path.join(RUNS_DIR, "attribution_warnings.log")


def _log_runner_event(event, pid, path):
    line = f"{datetime.now().isoformat()} PID={pid} event={event} path={path}\n"
    try:
        with open(_ATTRIBUTION_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(f"[runner-attribution] {line.rstrip()}", file=sys.stderr)


def write_json_exclusive(model_dir, base_name, data):
    for attempt in range(8):
        if attempt == 0:
            candidate = base_name
        else:
            candidate = f"{base_name[:-5]}_{time.time_ns() & 0xFFFFF:05x}.json"
        try:
            with open(os.path.join(model_dir, candidate), "x", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return os.path.join(model_dir, candidate)
        except FileExistsError:
            _log_runner_event("collision", os.getpid(), os.path.join(model_dir, candidate))
            continue
    _log_runner_event("exhausted", os.getpid(), os.path.join(model_dir, base_name))
    raise RuntimeError(f"No se pudo reservar un nombre de archivo unico: {base_name}")

EXTRA_PROMPT = (
    "Stability rules:\n"
    "1. If the target page is ALREADY open and you already know the "
    "required information, call done. Do NOT call navigate again.\n"
    "2. When calling done, the text must contain the actual observed "
    "values: copy the page <title> EXACTLY as shown, and the exact URL. "
    "Never repeat the user's task phrase as the answer.\n"
    "3. Calling navigate again to a URL you already visited is a CRITICAL "
    "ERROR and wastes the step budget. As soon as you observe the page "
    "<title> and URL, your IMMEDIATE and ONLY next action is done.\n"
)

WATCHDOG_LOG_SIG = "handler to return a non-None result"

ACL_ACTIONS = {
    "navigate",
    "click",
    "input",
    "search",
    "extract",
    "scroll",
    "wait",
    "go_back",
    "find_elements",
    "done",
}

WATCHDOG_SIG = "Expected at least one handler to return a non-None result, but none did!"
STATE_REQUEST_SIG = "BrowserStateRequestEvent"

RETRY_MAX = 3
RETRY_BACKOFF_S = [1, 2, 4]

STATE_TIMEOUT_S = "90"


class WatchdogSignalHandler(logging.Handler):
    def __init__(self, done_holder):
        super().__init__(level=logging.DEBUG)
        self.done_holder = done_holder
        self.done_holder.setdefault("log_matches", [])

    def emit(self, record):
        msg = record.getMessage()
        if WATCHDOG_LOG_SIG in msg and STATE_REQUEST_SIG in msg:
            self.done_holder["log_matches"].append(msg[:500])
            self.done_holder["watchdog_logged"] = True


# ---------------------------------------------------------------------------
# Instrumentación aditiva de contexto por step (experimento A/B href).
# NO modifica browser_use: monkey-patch de solo lectura en runtime, inactivo
# salvo que args.context_trace sea True. Si falla, nunca rompe la corrida.
# ---------------------------------------------------------------------------

_CTX_FLAG = "__browser_agent_ctx_trace__"
_CTX = {"holder": None, "attempt": None}
_TOKENIZER = {"tok": None, "err": None}


def _ctx_url_has(href, needle):
    href = (href or "").rsplit("#", 1)[0].rstrip("/")
    return needle in href


def _ctx_message_text(message):
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for p in content:
        if getattr(p, "type", None) == "text":
            parts.append(getattr(p, "text", "") or "")
    return "\n".join(parts)


def _ctx_token_count(text):
    try:
        if _TOKENIZER["tok"] is None and _TOKENIZER["err"] is None:
            try:
                from transformers import AutoTokenizer

                _TOKENIZER["tok"] = AutoTokenizer.from_pretrained("Qwen/Qwen3-1.7B")
            except Exception as exc:
                _TOKENIZER["err"] = str(exc)
        if _TOKENIZER["tok"] is not None:
            return len(_TOKENIZER["tok"].encode(text or "", add_special_tokens=False))
    except Exception:
        pass
    return None


def _ctx_collect_hrefs(dom_state):
    root = getattr(dom_state, "_root", None)
    hrefs = []
    interactive = 0
    if root is None:
        return hrefs, interactive
    stack = [root]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        if getattr(node, "is_interactive", False):
            interactive += 1
        original = getattr(node, "original_node", None)
        if original is not None:
            tag = (getattr(original, "tag_name", "") or "").lower()
            attrs = getattr(original, "attributes", None) or {}
            if tag == "a":
                h = attrs.get("href")
                if h and str(h).strip():
                    hrefs.append(str(h).strip())
        for child in getattr(node, "children", None) or []:
            stack.append(child)
    return hrefs, interactive


def _ctx_error(which, exc):
    holder = _CTX.get("holder")
    if holder is not None:
        holder.setdefault("ctx_errors", []).append(f"{which}: {type(exc).__name__}: {exc}")


def ensure_ctx_tracer():
    from browser_use.agent.message_manager.service import MessageManager

    if getattr(MessageManager, _CTX_FLAG, False):
        return
    orig_create = MessageManager.create_state_messages
    orig_get = MessageManager.get_messages

    def traced_create(self, browser_state_summary=None, *a, **k):
        result = orig_create(self, browser_state_summary, *a, **k)
        try:
            step_info = k.get("step_info")
            step = getattr(step_info, "step_number", None) if step_info is not None else None
            record = {
                "step": step,
                "url": getattr(browser_state_summary, "url", None),
                "title": getattr(browser_state_summary, "title", None),
                "hrefs": [],
                "href_count": 0,
                "interactive_elements": 0,
                "compare_a_present": False,
                "compare_b_present": False,
                "state_message": getattr(self, "last_state_message_text", None) or "",
            }
            dom = getattr(browser_state_summary, "dom_state", None)
            hrefs, interactive = _ctx_collect_hrefs(dom)
            record["hrefs"] = hrefs
            record["href_count"] = len(hrefs)
            record["interactive_elements"] = interactive
            record["compare_a_present"] = any(_ctx_url_has(h, "compare_a.html") for h in hrefs)
            record["compare_b_present"] = any(_ctx_url_has(h, "compare_b.html") for h in hrefs)
            holder = _CTX.get("holder")
            if holder is not None:
                holder["ctx_last_state"] = record
        except Exception as exc:
            _ctx_error("create_state_messages", exc)
        return result

    def traced_get(self):
        messages = orig_get(self)
        try:
            texts = [_ctx_message_text(m) for m in messages]
            context_text = "\n\n".join(texts)
            state = (_CTX.get("holder") or {}).get("ctx_last_state") or {}
            line = dict(state)
            line.update(
                {
                    "attempt": _CTX.get("attempt"),
                    "messages_count": len(messages),
                    "state_chars": len(state.get("state_message") or ""),
                    "state_tokens": _ctx_token_count(state.get("state_message") or ""),
                    "context_chars": len(context_text),
                    "context_tokens": _ctx_token_count(context_text),
                }
            )
            holder = _CTX.get("holder")
            if holder is not None:
                holder.setdefault("ctx_steps", []).append(line)
        except Exception as exc:
            _ctx_error("get_messages", exc)
        return messages

    MessageManager.create_state_messages = traced_create
    MessageManager.get_messages = traced_get
    setattr(MessageManager, _CTX_FLAG, True)


def parse_include_attributes(value):
    if value is None or value == "default":
        return None
    if value == "default+href":
        from browser_use.dom.views import DEFAULT_INCLUDE_ATTRIBUTES

        return list(DEFAULT_INCLUDE_ATTRIBUTES) + ["href"]
    if isinstance(value, list):
        return value
    return [v.strip() for v in str(value).split(",") if v.strip()]


def build_tools():
    tools = Tools()
    registry = tools.registry.registry.actions
    for name in list(registry.keys()):
        if name not in ACL_ACTIONS:
            tools.exclude_action(name)

    original_navigate = registry["navigate"]
    last_nav_url = {"value": None, "count": 0}

    def normalize_url(url):
        url = url.strip().split("#")[0]
        return url.rstrip("/").lower()

    async def _enrich_title(browser_session, old_result):
        try:
            title, href = await fetch_live_page_data(browser_session)
        except Exception:
            title = href = None
        if not title:
            return old_result
        ext = getattr(old_result, "extracted_content", None) or ""
        note = f'\n[REAL_TITLE] Trusted <title> from CDP: &quot;{title}&quot;. Report THIS exact title in done.text.'
        mem = f"REAL title of the loaded page document.title = &quot;{title}&quot;" + (f" | REAL url = {href}." if href else ".")
        return ActionResult(
            is_done=getattr(old_result, "is_done", False),
            error=None,
            long_term_memory=mem,
            extracted_content=(ext + note) if ext else note,
            include_in_memory=True,
        )

    async def guarded_navigate(params, browser_session):
        new_tab = bool(getattr(params, "new_tab", False))
        if not new_tab and last_nav_url["value"] is not None:
            if normalize_url(params.url) == last_nav_url["value"]:
                last_nav_url["count"] += 1
                if last_nav_url["count"] == 2:
                    return ActionResult(
                        error=(
                            f"BLOCKED: you already navigated to {params.url} and it is "
                            "already loaded. Re-navigating is a CRITICAL ERROR. Do NOT "
                            "call navigate again. The required title and URL are already "
                            "in your memory: call done now with the observed values."
                        )
                    )
                msg = (
                    f"ALREADY on {params.url} (navigated to it earlier in this session). "
                    "The page is already loaded. The title and URL are known. Your next "
                    "and ONLY action must be done with the observed values."
                )
                return ActionResult(extracted_content=msg, long_term_memory=msg)
        result = await original_navigate.function(params=params, browser_session=browser_session)
        if result is not None and getattr(result, "error", None) is None:
            last_nav_url["value"] = normalize_url(params.url)
            last_nav_url["count"] = 0
            result = await _enrich_title(browser_session, result)
        return result

    import browser_use.tools.registry.views as rv

    normalized_func, _param_model = tools.registry._normalize_action_function_signature(
        guarded_navigate,
        original_navigate.description,
        original_navigate.param_model,
    )
    registry["navigate"] = rv.RegisteredAction(
        name="navigate",
        description=original_navigate.description,
        function=normalized_func,
        param_model=original_navigate.param_model,
        terminates_sequence=original_navigate.terminates_sequence,
        domains=original_navigate.domains,
    )
    return tools


def build_llm(model, llm_timeout, num_ctx, num_predict):
    return Qwen3ChatOllama(
        model=model,
        host=OLLAMA_HOST,
        timeout=llm_timeout,
        ollama_options={
            "num_ctx": num_ctx,
            "num_predict": num_predict,
            "temperature": 0,
            "think": False,
        },
    )


def extract_title_from_state(state):
    if state is None:
        return None
    dom = getattr(state, "dom_state", None)
    rep = (
        dom.llm_representation()
        if dom is not None and hasattr(dom, "llm_representation")
        else None
    )
    if not rep:
        return None
    m = re.search(r"<title>(.*?)</title>", rep, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None


REAL_TAB = """JSON.stringify({title: document.title || '', href: location.href || ''})"""


async def fetch_live_page_data(browser_session):
    target_id = getattr(browser_session, "agent_focus_target_id", None)
    if not target_id:
        return None, None
    for attempt in range(3):
        try:
            cdp = await browser_session.get_or_create_cdp_session(target_id, focus=False)
            res = await cdp.cdp_client.send.Runtime.evaluate(
                params={"expression": REAL_TAB, "returnByValue": True},
                session_id=cdp.session_id,
            )
            value = None
            if isinstance(res, dict):
                value = (res.get("result") or {}).get("value")
            else:
                remote = getattr(res, "result", None)
                value = getattr(remote, "value", None) if remote is not None else None
            if value:
                data = json.loads(value)
                title = data.get("title") or None
                href = data.get("href") or None
                if title or href:
                    return title, href
        except Exception:
            pass
        await asyncio.sleep(1.0)
    return None, None


async def _on_done(done_holder, _history):
    try:
        agent = done_holder.get("agent")
        browser_session = getattr(agent, "browser_session", None) if agent else None
        title = None
        url = None
        if browser_session is not None:
            try:
                title, url = await fetch_live_page_data(browser_session)
            except Exception as exc:
                done_holder["callback_error"] = f"state_fetch: {type(exc).__name__}: {exc}"
            if not title:
                try:
                    state = await browser_session.get_browser_state_summary(
                        include_screenshot=False
                    )
                except Exception as exc:
                    done_holder["callback_error"] = f"summary_fetch: {type(exc).__name__}: {exc}"
                else:
                    if url is None:
                        url = getattr(state, "url", None)
                    summary_title = getattr(state, "title", None)
                    if summary_title and summary_title not in (
                        "example.com",
                        "Empty Tab",
                        "Page",
                        "Unknown page title",
                    ):
                        title = summary_title
        done_holder["title"] = title
        done_holder["url"] = url
    except Exception as exc:
        done_holder["callback_error"] = f"done_callback: {type(exc).__name__}: {exc}"


def build_agent(args, done_holder):
    llm = build_llm(
        args.model,
        args.llm_timeout,
        args.num_ctx,
        args.num_predict,
    )

    async def done_callback(_history):
        await _on_done(done_holder, _history)

    agent = Agent(
        task=args.task,
        llm=llm,
        tools=build_tools(),
        use_vision=False,
        use_thinking=False,
        max_actions_per_step=1,
        max_failures=args.max_failures,
        enable_planning=False,
        use_judge=False,
        flash_mode=True,
        max_history_items=args.max_history_items,
        generate_gif=False,
        step_timeout=args.step_timeout,
        llm_timeout=args.llm_timeout,
        message_compaction=False,
        enable_signal_handler=False,
        extend_system_message=EXTRA_PROMPT if args.extra_prompt else None,
        register_done_callback=done_callback,
        include_attributes=getattr(args, "include_attributes", None),
    )
    done_holder["agent"] = agent
    return agent


async def _run_single(args, done_holder):
    start = time.time()

    agent = build_agent(args, done_holder)
    error = None
    history = None

    watchdog_handler = WatchdogSignalHandler(done_holder)
    agent.logger.addHandler(watchdog_handler)
    if agent.browser_session is not None and agent.browser_session.logger is not None:
        agent.browser_session.logger.addHandler(watchdog_handler)

    try:
        history = await agent.run(max_steps=args.max_steps)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    done = False
    successful = None
    final = ""
    errors = [error] if error else []
    trace = []

    if history is not None:
        try:
            done = history.is_done()
            successful = history.is_successful()
            final = history.final_result() or ""
            errors = [e for e in history.errors() if e]
            for h in history.history:
                act = h.model_output.action if h.model_output else []
                actions = [
                    (a.model_dump(exclude_none=True, mode="json") if hasattr(a, "model_dump") else a)
                    for a in act
                ]
                trace.append(
                    {
                        "url": h.state.url if hasattr(h.state, "url") else None,
                        "actions": actions,
                        "result": [
                            r.model_dump() if hasattr(r, "model_dump") else str(r) for r in h.result
                        ],
                    }
                )
        except Exception as e:
            errors = [error, f"post_process: {type(e).__name__}: {e}"]
            done = False
            successful = None
            final = ""

    if not done and not final and not errors:
        errors = ["hit max_steps without done"]

    return {
        "history": history,
        "done": done,
        "successful": successful,
        "final": final,
        "errors": errors,
        "trace": trace,
        "elapsed_s": round(time.time() - start, 2),
    }


def has_watchdog_error(errors):
    return any(
        WATCHDOG_SIG in e and STATE_REQUEST_SIG in e
        for e in errors
        if e
    )


async def run_one(index, args):
    if args.headed:
        os.environ["BROWSER_USE_HEADLESS"] = "false"
    else:
        os.environ["BROWSER_USE_HEADLESS"] = "true"

    os.environ["TIMEOUT_BrowserStateRequestEvent"] = STATE_TIMEOUT_S

    attempts = []
    retries = []
    attempt_no = 0

    while True:
        attempt_no += 1
        done_holder = {}
        if getattr(args, "context_trace", False):
            ensure_ctx_tracer()
        _CTX["holder"] = done_holder
        _CTX["attempt"] = attempt_no
        res = await _run_single(args, done_holder)
        res["attempt"] = attempt_no

        attempts.append(
            {
                "attempt": attempt_no,
                "done": res["done"],
                "elapsed_s": res["elapsed_s"],
                "errors": res["errors"][:3],
            }
        )

        watchdog_seen = has_watchdog_error(res["errors"]) or done_holder.get("watchdog_logged", False)
        should_retry = (
            not res["done"]
            and watchdog_seen
            and len(attempts) < RETRY_MAX
        )

        if not should_retry:
            break

        wait_s = RETRY_BACKOFF_S[min(len(attempts) - 1, len(RETRY_BACKOFF_S) - 1)]
        retries.append(
            {
                "attempt": attempt_no,
                "wait_s": wait_s,
                "reason": res["errors"][0] if res["errors"] else "watchdog signature in logs",
            }
        )
        print(
            f"  [retry {len(attempts)}/{RETRY_MAX}] watchdog nav/state error -> "
            f"retrying in {wait_s}s (attempt {attempt_no} took {res['elapsed_s']}s)"
        )
        await asyncio.sleep(wait_s)

    _CTX["holder"] = None
    _CTX["attempt"] = None

    elapsed_total = round(sum(a["elapsed_s"] for a in attempts), 2)

    observed_title = done_holder.get("title")
    observed_url = done_holder.get("url")
    callback_error = done_holder.get("callback_error")

    if not observed_title and res["history"] is not None and res["history"].history:
        last_state = res["history"].history[-1].state
        observed_title = extract_title_from_state(last_state)
        observed_url = getattr(last_state, "url", None)

    final = res["final"]
    text_match = bool(final) and args.expected.lower() in final.lower()
    real_match = bool(observed_title) and args.expected.lower() in str(observed_title).lower()
    verified = res["done"] and text_match and real_match
    passed = verified

    if verified:
        failure_type = "success"
    elif res["done"]:
        failure_type = "verification_mismatch" if observed_title else "no_observed_title"
    elif watchdog_seen:
        failure_type = "navigation_watchdog"
    elif res["errors"]:
        failure_type = "run_error"
    else:
        failure_type = "max_steps_no_done"

    record = {
        "index": index,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": args.model,
        "task": args.task,
        "expected": args.expected,
        "experiment_condition": getattr(args, "experiment_condition", None),
        "include_attributes": getattr(args, "include_attributes", None),
        "context_trace": bool(getattr(args, "context_trace", False)),
        "settings": {
            "max_steps": args.max_steps,
            "max_failures": args.max_failures,
            "extra_prompt": args.extra_prompt,
            "num_ctx": args.num_ctx,
            "num_predict": args.num_predict,
            "headless": not args.headed,
            "acl_actions": sorted(ACL_ACTIONS),
            "state_timeout_s": STATE_TIMEOUT_S,
            "retry_max": RETRY_MAX,
            "retry_backoff_s": RETRY_BACKOFF_S,
        },
        "done": res["done"],
        "successful": res["successful"],
        "verified": verified,
        "passed": passed,
        "text_match": text_match,
        "real_match": real_match,
        "observed_title": observed_title,
        "observed_url": observed_url,
        "final_result": final,
        "errors": res["errors"][:10],
        "failure_type": failure_type,
        "attempts": attempts,
        "retries": retries,
        "callback_error": callback_error,
        "steps": len(res["trace"]),
        "elapsed_s": elapsed_total,
        "trace": res["trace"],
        "writer_pid": os.getpid(),
    }

    model_dir = os.path.join(RUNS_DIR, args.model.replace(":", "_"))
    os.makedirs(model_dir, exist_ok=True)
    base_name = (
        f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        f"_{index:03d}_p{os.getpid()}.json"
    )
    run_file = write_json_exclusive(model_dir, base_name, record)

    ctx_steps = done_holder.get("ctx_steps") or []
    if getattr(args, "context_trace", False) and ctx_steps:
        ctx_path = run_file[:-5] + ".ctx.jsonl"
        with open(ctx_path, "w", encoding="utf-8") as f:
            for _line in ctx_steps:
                _line = dict(_line)
                _line.update(
                    {
                        "run_index": index,
                        "condition": getattr(args, "experiment_condition", None),
                        "model": args.model,
                        "record_file": os.path.basename(run_file),
                    }
                )
                f.write(json.dumps(_line, ensure_ascii=False) + "\n")

    print()
    print(f"--- Corrida {index} ---")
    print(f"  done={res['done']} successful={res['successful']} verified={verified} passed={passed}")
    print(f"  observed_title={observed_title!r}")
    print(f"  steps={record['steps']} elapsed={elapsed_total}s ({len(attempts)} intento(s))")
    print(f"  final={final[:200]!r}")
    print(f"  failure_type={failure_type}")
    if res["errors"]:
        print(f"  errors={res['errors'][:3]}")
    print(f"  log={run_file}")

    return record


async def main():
    parser = argparse.ArgumentParser(
        description="Harness E2E: Qwen3 + browser-use + Playwright"
    )
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--model", default="qwen3:1.7b")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--expected", default=DEFAULT_EXPECTED)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--max-failures", type=int, default=3)
    parser.add_argument("--llm-timeout", type=int, default=240)
    parser.add_argument("--step-timeout", type=int, default=180)
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--num-predict", type=int, default=1024)
    parser.add_argument("--max-history-items", type=int, default=6)
    parser.add_argument(
        "--include-attributes",
        default=None,
        help="default | default+href | comma-separated list (experimento A/B href)",
    )
    parser.add_argument(
        "--context-trace",
        action="store_true",
        help="emitir per-step context JSONL junto a cada corrida run_*.ctx.jsonl",
    )
    parser.add_argument(
        "--experiment-condition",
        default=None,
        help="etiqueta de condicion (p.ej. A/B) para el experimento",
    )
    parser.add_argument(
        "--no-extra-prompt",
        dest="extra_prompt",
        action="store_false",
        help="Desactivar el prompt extra de estabilidad",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Mostrar Chromium (por defecto headless)",
    )
    args = parser.parse_args()
    args.include_attributes = parse_include_attributes(args.include_attributes)

    print("=" * 70)
    print("        E2E RUNNER  Qwen3 -> browser-use -> Chromium")
    print("=" * 70)
    print(f"model={args.model} runs={args.runs} task={args.task!r}")
    print()

    results = []

    for i in range(1, args.runs + 1):
        record = await run_one(i, args)
        results.append(record)

    passed = sum(1 for r in results if r["passed"])
    done = sum(1 for r in results if r["done"])
    verified = sum(1 for r in results if r.get("verified"))

    print()
    print("=" * 70)
    print("                    RESUMEN")
    print("=" * 70)
    print(f"corridas={len(results)}")
    print(f"done={done}/{len(results)}")
    print(f"verified={verified}/{len(results)}")
    print(f"passed={passed}/{len(results)}")
    if results:
        print(f"tasa de exito={passed / len(results) * 100:.0f}%")
        avg = sum(r["elapsed_s"] for r in results) / len(results)
        print(f"tiempo promedio={avg:.1f}s")
        breakdown = {}
        for r in results:
            ft = r.get("failure_type", "unknown")
            breakdown[ft] = breakdown.get(ft, 0) + 1
        print("fallos por tipo:")
        for ft, count in sorted(breakdown.items()):
            print(f"  - {ft}: {count}")

    summary_file = write_json_exclusive(
        os.path.join(RUNS_DIR, args.model.replace(":", "_")),
        f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}_p{os.getpid()}.json",
        {
            "model": args.model,
            "runs": len(results),
            "done": done,
            "verified": verified,
            "passed": passed,
            "success_rate": round(passed / len(results) * 100, 1) if results else 0,
            "failure_breakdown": breakdown if results else {},
            "results": [
                {k: r[k] for k in ("index", "passed", "verified", "failure_type", "steps", "elapsed_s", "final_result")}
                for r in results
            ],
        },
    )
    print(f"summary={summary_file}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[RUNNER] Interrumpido por usuario.")
        sys.exit(1)