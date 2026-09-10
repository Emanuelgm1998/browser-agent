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
    }

    model_dir = os.path.join(RUNS_DIR, args.model.replace(":", "_"))
    os.makedirs(model_dir, exist_ok=True)
    run_file = os.path.join(
        model_dir,
        f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{index:03d}.json",
    )
    with open(run_file, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

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

    summary_file = os.path.join(
        RUNS_DIR,
        args.model.replace(":", "_"),
        f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(
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
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"summary={summary_file}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[RUNNER] Interrumpido por usuario.")
        sys.exit(1)