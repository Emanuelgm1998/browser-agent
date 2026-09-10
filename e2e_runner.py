import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from browser_use import Agent
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
)

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


def build_tools():
    tools = Tools()
    for name in list(tools.registry.registry.actions.keys()):
        if name not in ACL_ACTIONS:
            tools.exclude_action(name)
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


async def run_one(index, args):
    start = time.time()

    if args.headed:
        os.environ["BROWSER_USE_HEADLESS"] = "false"
    else:
        os.environ["BROWSER_USE_HEADLESS"] = "true"

    llm = build_llm(
        args.model,
        args.llm_timeout,
        args.num_ctx,
        args.num_predict,
    )

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
    )

    trace = []
    history = None
    error = None

    try:
        history = await agent.run(max_steps=args.max_steps)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    elapsed = round(time.time() - start, 2)

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

    passed = bool(final) and args.expected.lower() in final.lower()

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
        },
        "done": done,
        "successful": successful,
        "passed": passed,
        "final_result": final,
        "errors": errors[:10],
        "steps": len(trace),
        "elapsed_s": elapsed,
        "trace": trace,
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
    print(f"  done={done} successful={successful} passed={passed}")
    print(f"  steps={record['steps']} elapsed={elapsed}s")
    print(f"  final={final[:200]!r}")
    if errors:
        print(f"  errors={errors[:3]}")
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

    print()
    print("=" * 70)
    print("                    RESUMEN")
    print("=" * 70)
    print(f"corridas={len(results)}")
    print(f"done={done}/{len(results)}")
    print(f"passed={passed}/{len(results)}")
    if results:
        print(f"tasa de exito={passed / len(results) * 100:.0f}%")
        avg = sum(r["elapsed_s"] for r in results) / len(results)
        print(f"tiempo promedio={avg:.1f}s")

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
                "passed": passed,
                "success_rate": round(passed / len(results) * 100, 1) if results else 0,
                "results": [
                    {k: r[k] for k in ("index", "passed", "done", "steps", "elapsed_s", "final_result")}
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