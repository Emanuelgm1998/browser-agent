"""Benchmark v0.2 orchestrator.

Reuses the stabilized harness (e2e_runner.run_one) WITHOUT touching it:
per task it injects an arguments namespace and collects the harness record,
then applies benchmark-level objective verification, failure classification,
recovery-cost metrics and per-run JSON persistence.

Modes:
  default     run tasks x runs per model, then write report
  --dry-run   validate configuration + server without launching LLM runs
  --report-only  re-aggregate existing runs and regenerate reports
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from benchmark import BENCHMARK_VERSION
from benchmark.fixtures_server import FixtureServer
from benchmark.tasks import tasks_filter, TASKS
from benchmark.verifiers import Verifier, classify_failure, parse_trace, norm_lower
from benchmark.report import build_report_data, format_report, format_comparison


def model_dirname(model):
    return model.replace(":", "_")


def resolve_include_attributes(value):
    if value is None or value == "default":
        return None
    if value == "default+href":
        from browser_use.dom.views import DEFAULT_INCLUDE_ATTRIBUTES

        return list(DEFAULT_INCLUDE_ATTRIBUTES) + ["href"]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def write_json_exclusive(directory, base_name, data):
    os.makedirs(directory, exist_ok=True)
    for attempt in range(16):
        if attempt == 0:
            candidate = base_name
        else:
            candidate = f"{base_name[:-5]}_{time.time_ns() & 0xFFFFF:05x}.json"
        try:
            with open(os.path.join(directory, candidate), "x", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(f"No se pudo reservar nombre de archivo: {base_name}")


def build_harness_args(task, model, base_url, overrides):
    return SimpleNamespace(
        model=model,
        task=task.prompt.format(base=base_url),
        expected="__BENCHMARK_MARKER__",
        max_steps=overrides.get("max_steps") or task.max_steps,
        max_failures=overrides.get("max_failures", 3),
        llm_timeout=overrides.get("llm_timeout", 240),
        step_timeout=overrides.get("step_timeout", 180),
        num_ctx=overrides.get("num_ctx", 8192),
        num_predict=overrides.get("num_predict", 1024),
        max_history_items=overrides.get("max_history_items", 8),
        extra_prompt=not overrides.get("no_extra_prompt", False),
        headed=overrides.get("headed", False),
        include_attributes=overrides.get("include_attributes"),
        context_trace=overrides.get("context_trace", False),
        experiment_condition=overrides.get("experiment_condition"),
    )


def build_benchmark_record(task, model, harness_record, checks, fcat, run_no, exec_ts):
    trace_meta = parse_trace(harness_record.get("trace") or [])
    visited = trace_meta["visited"]
    final_url = harness_record.get("observed_url") or (visited[-1] if visited else None)
    exec_ts = exec_ts or datetime.now()

    errors = [e for e in (harness_record.get("errors") or []) if e]
    errors_low = norm_lower(" ".join(errors))
    timeout = (
        "timeout" in errors_low
        or "timed out" in errors_low
        or float(harness_record.get("elapsed_s", 0)) >= task.timeout_s
    )

    retry_list = harness_record.get("retries") or []
    retries = len(retry_list)
    retry_delay_total = round(sum(float(r.get("wait_s", 0)) for r in retry_list), 2)
    # recovery_events counts REAL recovery (watchdog retries) only; a clean
    # single-attempt run must report 0. Redundant guarded-nav blocks are kept
    # as a separate diagnostic (navigation_retries).
    recovery_events = len(harness_record.get("attempts") or []) - 1

    success = fcat == "success"
    data_ok = bool(checks["data_ok"])
    synth_ok = checks["synthesis_ok"]  # True/False, or None when the task has no synthesis
    verified_all = bool(checks["destination_ok"]) and data_ok and (
        bool(synth_ok) if synth_ok is not None else True
    )
    done_false_positive = (
        bool(harness_record.get("done"))
        and not verified_all
        and fcat in {"interaction_failure", "extraction_failure", "verification_failure", "model_output_failure"}
    )
    model_family = model.split(":")[0] if ":" in model else model

    return {
        "run_id": f"{model_dirname(model)}_{task.task_id}_{run_no:02d}_{exec_ts.strftime('%Y%m%d_%H%M%S')}",
        "benchmark_version": BENCHMARK_VERSION,
        "task_id": task.task_id,
        "tier": task.tier,
        "task_name": task.name,
        "model": model,
        "model_family": model_family,
        "experiment_condition": harness_record.get("experiment_condition"),
        "include_attributes": harness_record.get("include_attributes"),
        "timestamp": exec_ts.isoformat(timespec="seconds"),
        "done": bool(harness_record.get("done")),
        "success": success,
        "verified": data_ok,
        "verified_all": verified_all,
        "destination_ok": bool(checks["destination_ok"]),
        "data_ok": data_ok,
        "synthesis_ok": synth_ok,
        "failure_category": fcat,
        "steps": int(harness_record.get("steps", 0)),
        "max_steps": task.max_steps,
        "time_seconds": float(harness_record.get("elapsed_s", 0)),
        "timeout": timeout,
        "attempts": len(harness_record.get("attempts") or []),
        "retries": retries,
        "recovery_events": recovery_events,
        "retry_delay_total": retry_delay_total,
        "navigation_retries": trace_meta["nav_retries"],
        "recovery_success": success and recovery_events > 0,
        "recovery_failed": (not success) and recovery_events > 0,
        "navigation_count": trace_meta["nav_count"],
        "final_url": final_url,
        "observed_title": harness_record.get("observed_title"),
        "final_result": harness_record.get("final_result") or "",
        "errors": errors[:8],
        "trace_actions": trace_meta["action_counts"],
        "visited_urls": visited,
        "verification_details": checks["groups"],
        "done_false_positive": done_false_positive,
    }


async def run_model(args, model, server, verifier, selected):
    runs_dir = Path(args.runs_dir) / model_dirname(model)
    os.makedirs(runs_dir, exist_ok=True)
    overrides = {
        "max_steps": args.max_steps,
        "max_failures": args.max_failures,
        "llm_timeout": args.llm_timeout,
        "step_timeout": args.step_timeout,
        "num_ctx": args.num_ctx,
        "num_predict": args.num_predict,
        "max_history_items": args.max_history_items,
        "no_extra_prompt": not args.extra_prompt,
        "headed": args.headed,
        "include_attributes": args.include_attributes,
        "context_trace": args.context_trace,
        "experiment_condition": args.experiment_condition,
    }
    import e2e_runner as harness

    print("=" * 70)
    print(f"  BENCHMARK {BENCHMARK_VERSION}  model={model}")
    print("=" * 70)

    global_seq = 0
    totals = {"runs": 0, "success": 0}
    for task in selected:
        harness_args = build_harness_args(task, model, server.base_url, overrides)
        print()
        print(f"## {task.task_id} (Tier {task.tier}) {task.name}")
        print(f"   prompt: {task.prompt.format(base=server.base_url)}")
        task_success = 0
        started = time.time()
        for run_no in range(1, args.runs + 1):
            global_seq += 1
            try:
                harness_record = await harness.run_one(global_seq, harness_args)
            except Exception as exc:
                harness_record = {
                    "done": False,
                    "successful": False,
                    "verified": False,
                    "passed": False,
                    "errors": [f"{type(exc).__name__}: {exc}"],
                    "steps": 0,
                    "elapsed_s": 0.0,
                    "attempts": [],
                    "retries": [],
                    "trace": [],
                    "final_result": "",
                    "observed_title": None,
                    "observed_url": None,
                }
                print(f"  [{task.task_id} run {run_no}/{args.runs}] HARNESS EXCEPTION: {type(exc).__name__}: {exc}")
            verify_record = _classification_record(harness_record, task)
            checks = await verifier.verify_task(task, verify_record)
            fcat = classify_failure(task, verify_record, checks)
            exec_ts = datetime.now()
            bench = build_benchmark_record(task, model, harness_record, checks, fcat, run_no, exec_ts)
            filename = write_json_exclusive(
                str(runs_dir),
                f"run_{exec_ts.strftime('%Y%m%d_%H%M%S')}_{task.task_id}_{run_no:02d}.json",
                bench,
            )
            task_success += 1 if bench["success"] else 0
            totals["runs"] += 1
            totals["success"] += 1 if bench["success"] else 0
            print(
                f"  [{task.task_id} run {run_no}/{args.runs}] "
                f"success={bench['success']} done={bench['done']} "
                f"verified={bench['verified']} failure={bench['failure_category']} "
                f"steps={bench['steps']} time={bench['time_seconds']:.0f}s "
                f"retries={bench['retries']} -> {filename}"
            )
        elapsed = time.time() - started
        print(f"  {task.task_id} total: {task_success}/{args.runs} success in {elapsed:.0f}s")

    print()
    print(f"MODEL {model}: {totals['success']}/{totals['runs']} success")


def _classification_record(harness_record, task):
    rec = dict(harness_record)
    rec["max_steps"] = task.max_steps
    visited = parse_trace(harness_record.get("trace") or [])["visited"]
    rec["visited_urls"] = visited
    rec["final_url"] = harness_record.get("observed_url") or (visited[-1] if visited else None)
    return rec


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=f"Browser Agent Eval benchmark {BENCHMARK_VERSION}")
    p.add_argument("--runs", type=int, default=5, help="runs per task (default 5)")
    p.add_argument("--models", nargs="+", default=["qwen3:1.7b"])
    p.add_argument("--tasks", nargs="+", default=None, help="e.g. T1 T4 T7")
    p.add_argument("--tiers", nargs="+", default=None, help="e.g. 1 2 3")
    p.add_argument("--port", type=int, default=0, help="fixture server port (0=auto)")
    p.add_argument("--runs-dir", default=str(Path(__file__).resolve().parent / "runs"))
    p.add_argument("--reports-dir", default=str(Path(__file__).resolve().parent / "reports"))
    p.add_argument("--report-only", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--headed", action="store_true", help="show agent Chromium")
    p.add_argument("--no-extra-prompt", dest="extra_prompt", action="store_false", default=True)
    p.add_argument("--max-steps", type=int, default=0, help="override task step budget (0=use task)")
    p.add_argument("--max-failures", type=int, default=3)
    p.add_argument("--llm-timeout", type=int, default=240)
    p.add_argument("--step-timeout", type=int, default=180)
    p.add_argument("--num-ctx", type=int, default=8192)
    p.add_argument("--num-predict", type=int, default=1024)
    p.add_argument("--max-history-items", type=int, default=8)
    p.add_argument(
        "--include-attributes",
        default=None,
        help="default | default+href | comma-separated list (experimento A/B href)",
    )
    p.add_argument("--context-trace", action="store_true", help="emitir per-step context JSONL")
    p.add_argument("--experiment-condition", default=None, help="etiqueta de condicion (p.ej. A/B)")
    p.add_argument("--verifier-headless", dest="verifier_headless", action="store_true", default=True)
    return p.parse_args(argv)


async def amain(args):
    selected = tasks_filter(task_ids=args.tasks, tiers=args.tiers)
    if not selected:
        print("No tasks selected.")
        return 2
    print(f"selected tasks: {[t.task_id for t in selected]}")
    if args.dry_run:
        print("dry-run: configuration OK")
        print(f"  models={args.models} runs={args.runs} tiers={[t.tier for t in selected]}")
        with FixtureServer(args.port) as srv:
            print(f"  fixtures OK at {srv.base_url}")
        return 0

    verifier = Verifier(headless=args.verifier_headless)
    await verifier._ensure()
    try:
        for model in args.models:
            with FixtureServer(args.port) as server:
                await run_model(args, model, server, verifier, selected)
    finally:
        await verifier.close()
    generate_reports(args)
    return 0


def generate_reports(args, ts=None):
    ts = ts or datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(args.reports_dir, exist_ok=True)
    data = build_report_data(args.runs_dir, args.models)
    for model, model_data in data["models_detail"].items():
        text = format_report(model, model_data)
        path = os.path.join(args.reports_dir, f"report_{model_dirname(model)}_{ts}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"\nreport -> {path}")
        print(text)
    if len(data["models_detail"]) > 1:
        cmp_text = format_comparison(data)
        path = os.path.join(args.reports_dir, f"comparison_{ts}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(cmp_text)
        print(f"\ncomparison -> {path}")
        print(cmp_text)


def main(argv=None):
    args = parse_args(argv)
    args.include_attributes = resolve_include_attributes(args.include_attributes)
    if args.report_only:
        generate_reports(args)
        return 0
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())