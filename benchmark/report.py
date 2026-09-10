"""Benchmark v0.2 aggregation and reporting (Fases 8, 10, 11).

Builds per-run, per-task, per-tier and per-model metrics from the JSON runs
written by benchmark/runner.py, then formats them without hiding anything
behind a single percentage.
"""
import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from benchmark import BENCHMARK_VERSION
from benchmark.tasks import TASKS_BY_ID


def _pct(num, den):
    return round(num / den * 100, 1) if den else 0.0


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else 0.0


def load_runs(runs_dir):
    runs = []
    root = Path(runs_dir)
    if not root.is_dir():
        return runs
    for model_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for f in sorted(model_dir.glob("run_*.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    runs.append(json.load(fh))
            except Exception:
                continue
    return runs


def build_report_data(runs_dir, models):
    runs = load_runs(runs_dir)
    detail = {}
    for model in models:
        detail[model] = _model_summary([r for r in runs if r.get("model") == model])
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "runs_dir": runs_dir,
        "models_configured": list(models),
        "models_present": {m: d for m, d in detail.items() if d["runs"] > 0},
        "models_detail": {m: d for m, d in detail.items() if d["runs"] > 0},
    }


def _model_summary(runs):
    by_task = {}
    for task_id in sorted({r["task_id"] for r in runs}):
        truns = [r for r in runs if r["task_id"] == task_id]
        by_task[task_id] = task_metrics(truns)
    by_tier = {}
    for tier in sorted({r["tier"] for r in runs}):
        by_tier[str(tier)] = tier_metrics([r for r in runs if r["tier"] == tier])
    return {
        "runs": len(runs),
        "tasks": by_task,
        "tiers": by_tier,
        "overall": overall_metrics(runs),
        "failure_distribution": failure_distribution(runs),
        "recovery": recovery_metrics(runs),
    }


def _base(runs):
    return {
        "runs": len(runs),
        "done": sum(bool(r.get("done")) for r in runs),
        "verified": sum(bool(r.get("verified")) for r in runs),
        "success": sum(bool(r.get("success")) for r in runs),
    }


def overall_metrics(runs):
    m = _base(runs)
    if not runs:
        return m
    m["success_rate"] = _pct(m["success"], m["runs"])
    m["done_rate"] = _pct(m["done"], m["runs"])
    m["verification_rate"] = _pct(m["verified"], m["runs"])
    m["avg_steps"] = _avg([r.get("steps") for r in runs])
    m["avg_time"] = _avg([r.get("time_seconds") for r in runs])
    m["avg_retries"] = _avg([r.get("retries") for r in runs])
    m["avg_recovery_events"] = _avg([r.get("recovery_events") for r in runs])
    return m


def task_metrics(runs):
    m = _base(runs)
    if not runs:
        return m
    m["success_rate"] = _pct(m["success"], m["runs"])
    m["done_rate"] = _pct(m["done"], m["runs"])
    m["verification_rate"] = _pct(m["verified"], m["runs"])
    m["avg_steps"] = _avg([r.get("steps") for r in runs])
    m["avg_time"] = _avg([r.get("time_seconds") for r in runs])
    m["avg_retries"] = _avg([r.get("retries") for r in runs])
    m["recovery_runs"] = sum(bool(r.get("recovery_events")) for r in runs)
    m["recovery_rate"] = _pct(m["recovery_runs"], m["runs"])
    m["clean_runs"] = sum(bool(r.get("success") and not r.get("recovery_events")) for r in runs)
    task = TASKS_BY_ID.get(runs[0].get("task_id"))
    m["synthesis_defined"] = bool(task and task.synthesis)
    if m["synthesis_defined"]:
        m["synthesis_rate"] = _pct(sum(bool(r.get("synthesis_ok")) for r in runs), m["runs"])
    m["failure_distribution"] = failure_distribution(runs)
    return m


def tier_metrics(runs):
    m = task_metrics(runs)  # same shape; reuse
    return m


def failure_distribution(runs):
    dist = defaultdict(int)
    for r in runs:
        dist[r.get("failure_category") or "unknown"] += 1
    return dict(sorted(dist.items()))


def recovery_metrics(runs):
    events = runs
    return {
        "runs_with_recovery": sum(bool(r.get("recovery_events")) for r in events),
        "recovery_rate": _pct(sum(bool(r.get("recovery_events")) for r in events), len(events)),
        "avg_retries": _avg([r.get("retries") for r in events]),
        "avg_recovery_events": _avg([r.get("recovery_events") for r in events]),
        "retry_delay_total_s": sum(float(r.get("retry_delay_total") or 0) for r in events),
        "recovery_success": sum(bool(r.get("recovery_success")) for r in events),
        "recovery_failed": sum(bool(r.get("recovery_failed")) for r in events),
        "nav_redundant_total": sum(int(r.get("navigation_retries") or 0) for r in events),
    }


def _fmt_task_line(tid, t, meta, indent=2):
    pad = " " * indent
    lines = [f"{pad}{tid} {meta['name']} (Tier {meta['tier']})"]
    lines.append(
        f"{pad}  runs={t['runs']} success={t['success']}/{t['runs']} ({t['success_rate']}%) "
        f"done={t['done']}/{t['runs']} ({t['done_rate']}%) verified={t['verified']}/{t['runs']} ({t['verification_rate']}%)"
    )
    lines.append(
        f"{pad}  avg_steps={t['avg_steps']} avg_time={t['avg_time']}s avg_retries={t['avg_retries']} "
        f"recovery_rate={t['recovery_rate']}% clean_runs={t['clean_runs']}/{t['runs']}"
    )
    if t.get("synthesis_defined"):
        lines.append(f"{pad}  synthesis_rate={t['synthesis_rate']}% (medida por separado)")
    if t["failure_distribution"]:
        lines.append(f"{pad}  failures=" + json.dumps(t["failure_distribution"], ensure_ascii=False))
    return "\n".join(lines)


def format_report(model, data):
    by_tier = data["tiers"]
    tasks = data["tasks"]

    lines = []
    lines.append("=" * 62)
    lines.append(f"BROWSER AGENT EVAL v{BENCHMARK_VERSION}")
    lines.append("=" * 62)
    lines.append(f"MODEL: {model}")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Total runs: {data['overall']['runs']}")
    lines.append("")

    tier_nums = sorted(int(k) for k in by_tier)
    for tier_int in tier_nums:
        t = by_tier.get(str(tier_int))
        lines.append(f"\nTier {tier_int}")
        lines.append("-" * 40)
        for tid in sorted(tasks.keys()):
            meta = TASKS_BY_ID.get(tid)
            if meta is None or meta.tier != tier_int:
                continue
            lines.append(_fmt_task_line(tid, tasks[tid], {"name": meta.name, "tier": meta.tier}))
        if t:
            lines.append(
                f"  Tier {tier_int} totals: success={t['success']}/{t['runs']} ({t['success_rate']}%) "
                f"done={t['done']}/{t['runs']} verified={t['verified']}/{t['runs']} "
                f"avg_time={t['avg_time']}s avg_steps={t['avg_steps']} avg_retries={t['avg_retries']} "
                f"recovery_rate={t['recovery_rate']}%"
            )
            if t["failure_distribution"]:
                lines.append(f"  Tier {tier_int} failures=" + json.dumps(t["failure_distribution"], ensure_ascii=False))

    o = data["overall"]
    lines.append("\n\nOVERALL")
    lines.append("-" * 40)
    lines.append(
        f"success {o['success']}/{o['runs']} ({o['success_rate']}%)  "
        f"done {o['done']}/{o['runs']} ({o['done_rate']}%)  "
        f"verified {o['verified']}/{o['runs']} ({o['verification_rate']}%)"
    )
    lines.append(f"avg_steps={o['avg_steps']} avg_time={o['avg_time']}s avg_retries={o['avg_retries']}")

    lines.append("\nFAILURE DISTRIBUTION")
    lines.append("-" * 40)
    for cat, n in data["failure_distribution"].items():
        lines.append(f"  {cat}: {n}")

    rec = data["recovery"]
    lines.append("\nRECOVERY")
    lines.append("-" * 40)
    lines.append(f"runs_with_recovery={rec['runs_with_recovery']}/{data['overall']['runs']} ({rec['recovery_rate']}%)")
    lines.append(f"avg_retries={rec['avg_retries']} avg_recovery_events={rec['avg_recovery_events']} total_retry_delay={rec['retry_delay_total_s']}s")
    lines.append(f"recovery_success={rec['recovery_success']} recovery_failed={rec['recovery_failed']}")
    lines.append(f"nav_redundant_blocks_total={rec['nav_redundant_total']} (navegaciones repetidas cortadas por el dedupe guard)")

    lines.append("\nMODEL CAPABILITY SEPARATION (evidence, not judgement)")
    lines.append("-" * 40)
    lines.append("navigation vs model attribution: see failure categories above")
    return "\n".join(lines) + "\n"


def format_comparison(data):
    models = list(data["models_present"].keys())
    rows = []
    headers = ["Metric"] + models
    tiers = sorted({int(k) for m in data["models_present"].values() for k in m["tiers"]})
    for tier in tiers:
        row = {m: _fmt_sr(data["models_present"][m]["tiers"].get(str(tier))) for m in models}
        rows.append((f"Tier {tier} success", row))
    row = {m: _fmt_sr(data["models_present"][m]["overall"]) for m in models}
    rows.append(("Overall success", row))
    row = {m: _fmt_misc(data["models_present"][m]["overall"]) for m in models}
    rows.append(("Overall verified/done", row))
    for field, label in (("avg_retries", "Avg retries"), ("avg_time", "Avg time (s)"), ("avg_steps", "Avg steps")):
        rows.append((label, {m: data["models_present"][m]["overall"][field] for m in models}))
    rows.append(("Recovery rate %", {m: data["models_present"][m]["recovery"]["recovery_rate"] for m in models}))

    lines = ["=" * 62, f"BROWSER AGENT EVAL v{BENCHMARK_VERSION} - COMPARACION POR MODELO", "=" * 62]
    header = f"{'Metric':<28}" + "".join(f"{m:>16}" for m in models)
    lines.append(header)
    lines.append("-" * len(header))
    for label, row in rows:
        lines.append(f"{label:<28}" + "".join(f"{str(row.get(m, '')):>16}" for m in models))
    return "\n".join(lines) + "\n"


def _fmt_sr(metrics):
    if not metrics:
        return "n/a"
    return _pct(metrics["success"], metrics["runs"])


def _fmt_misc(o):
    return f"{o['verified']}/{o['done']}/{o['runs']}"