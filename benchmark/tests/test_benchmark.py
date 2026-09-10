"""Benchmark v0.2 unit tests. No LLM required.

Run directly:  python benchmark/tests/test_benchmark.py
Or under pytest:  pytest benchmark/tests/test_benchmark.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from benchmark.fixtures_server import FixtureServer
from benchmark.tasks import tasks_filter, TASKS_BY_ID
from benchmark.verifiers import (
    Verifier,
    classify_failure,
    extract_json,
    norm,
    parse_trace,
    path_of,
)
from benchmark.report import build_report_data, format_report, format_comparison

_PASS = []
_FAIL = []


def check(name, cond):
    if cond:
        _PASS.append(name)
        print(f"  ok  {name}")
    else:
        _FAIL.append(name)
        print(f"FAIL  {name}")


async def test_fixture_server():
    print("fixture server")
    with FixtureServer(0) as srv:
        import urllib.request

        def get(page):
            with urllib.request.urlopen(srv.url(page), timeout=10) as r:
                return r.read().decode("utf-8")

        html = get("anchor.html")
        check("anchor served", "#K7QZ4" in html and "4A9-MN72" in html)
        check("dest served", "You have arrived" in get("dest.html"))
        check("confirm served", "SUBMISSION_OK" in get("confirm.html"))
        check("search served", 'action="results.html"' in get("search.html"))
        check("results served", "article2.html" in get("results.html"))
        check("compare pages", "$99" in get("compare_a.html") and "$79" in get("compare_b.html"))


def test_extract_json():
    print("extract_json")
    check("fenced json", extract_json("```json\n{\"a\": 1}\n```") == {"a": 1})
    check("json in prose", isinstance(extract_json('Answer: [{"x":1}] done'), list))
    check("none", extract_json("no json here") is None)


def test_helpers():
    print("helpers")
    check("norm", norm("  a\nb  ") == "a b")
    check("path_of", path_of("http://x.test/a/b.html") == "/a/b.html")
    check(
        "parse_trace",
        parse_trace(
            [
                {
                    "url": "http://x.test/start.html",
                    "actions": [{"navigate": {"url": "http://x.test/start.html"}}],
                    "result": [{}],
                },
                {
                    "url": "http://x.test/dest.html",
                    "actions": [{"click": {}}],
                    "result": [{"extracted_content": "ALREADY on ..."}],
                },
            ]
        )
        == {
            "action_counts": {"navigate": 1, "click": 1},
            "visited": ["http://x.test/start.html", "http://x.test/dest.html"],
            "nav_count": 1,
            "nav_retries": 1,
        },
    )


def _lr(overrides=None):
    rec = {
        "done": True,
        "errors": [],
        "steps": 3,
        "max_steps": 10,
        "timeout": False,
        "final_result": "ok",
        "observed_title": None,
        "observed_url": None,
        "final_url": None,
        "trace": [
            {
                "url": "http://x.test/start.html",
                "actions": [{"navigate": {}}],
                "result": [],
            }
        ],
    }
    rec.update(overrides or {})
    rec["visited_urls"] = parse_trace(rec["trace"])["visited"]
    return rec


def _click_trace():
    return [{"url": "u", "actions": [{"click": {}}, {"navigate": {}}], "result": []}]


def _checks(dest=True, data=True, synth=None):
    return {"destination_ok": dest, "data_ok": data, "synthesis_ok": synth}


def test_classify():
    print("classify_failure")
    t = TASKS_BY_ID["T4"]
    check("success", classify_failure(t, _lr(overrides={"trace": _click_trace()}), _checks(True, True)) == "success")
    check(
        "interaction_failure (no click)",
        classify_failure(
            t,
            _lr(overrides={"trace": [{"url": "u", "actions": [{"navigate": {}}], "result": []}]}),
            _checks(),
        )
        == "interaction_failure",
    )
    check(
        "interaction_failure even if dest ok (direct nav cheat)",
        classify_failure(t, _lr(overrides={}), _checks(True, True)) == "interaction_failure",
    )
    # direct nav to require dest.html, dest ok but no click -> interaction failure
    rec = _lr(overrides={"trace": [
        {"url": "u", "actions": [{"navigate": {"url": "http://x/dest.html"}}], "result": []},
    ]})
    check("cheat detected", classify_failure(t, rec, _checks(True, True)) == "interaction_failure")
    check(
        "navigation_failure",
        classify_failure(t, _lr(overrides={"trace": [{"url": "u", "actions": [{"click": {}}, {"navigate": {}}], "result": []}]}), _checks(False, False))
        == "navigation_failure",
    )
    check(
        "extraction_failure",
        classify_failure(t, _lr(overrides={"trace": [{"url": "u", "actions": [{"click": {}}, {"navigate": {}}], "result": []}]}), _checks(True, False))
        == "extraction_failure",
    )
    tm = TASKS_BY_ID["T10"]
    check(
        "synthesis_failure",
        classify_failure(tm, _lr(overrides={}), _checks(True, True, False)) == "synthesis_failure",
    )
    check(
        "max_steps",
        classify_failure(t, _lr(overrides={"done": False, "steps": 10, "errors": [], "final_result": ""}), _checks())
        == "max_steps",
    )
    check(
        "model_output_failure",
        classify_failure(t, _lr(overrides={"done": False, "steps": 5, "errors": [], "final_result": ""}), _checks())
        == "model_output_failure",
    )
    check(
        "infrastructure_failure",
        classify_failure(t, _lr(overrides={"done": False, "steps": 5, "errors": ["ModelProviderError: bad json"], "final_result": ""}), _checks())
        == "infrastructure_failure",
    )
    check(
        "timeout",
        classify_failure(t, _lr(overrides={"done": False, "timeout": True}), _checks())
        == "timeout",
    )


async def test_verifier_checks():
    print("verifier live checks (real browser + local server)")
    with FixtureServer(0) as srv:
        v = Verifier(headless=True)
        try:
            base = srv.base_url

            rec = {
                "done": True,
                "final_url": srv.url("dest.html"),
                "observed_title": "Benchmark Destination",
                "final_result": "The title is Benchmark Destination and You have arrived.",
                "visited_urls": [srv.url("dest.html")],
            }
            r = await v._check({"type": "title_exact", "expected": "Benchmark Destination"}, rec)
            check("title_exact ok", r["ok"])
            r = await v._check({"type": "title_exact", "expected": "WRONG"}, rec)
            check("title_exact fail", not r["ok"])
            r = await v._check({"type": "final_url_equals", "expected": "dest.html", "mode": "suffix"}, rec)
            check("url suffix ok", r["ok"] is True)
            r = await v._check({"type": "text_contains", "expected": "arrived", "where": "final"}, rec)
            check("text_contains final", r["ok"])
            r = await v._check({"type": "element_exists", "selector": "#arrived"}, rec)
            check("element_exists #arrived", r["ok"] is True)
            r = await v._check({"type": "element_exists", "selector": "#nope"}, rec)
            check("element_exists missing", r["ok"] is False)
            r = await v._check({"type": "source_data_match", "expected": ["You have arrived"]}, rec)
            check("source_data_match", r["ok"])

            ranchor = {
                "done": True,
                "final_url": srv.url("anchor.html"),
                "observed_title": "Anchor Page",
                "final_result": "model id is 4A9-MN72",
                "visited_urls": [srv.url("anchor.html")],
            }
            r = await v._check(
                {"type": "reached_page_price_match", "allowed_urls": ["article1.html"], "expected_prices": ["$42"]},
                {**ranchor, "final_url": srv.url("article1.html"), "final_result": "price $42"},
            )
            check("reached_page_price_match", r["ok"])
            r = await v._check(
                {"type": "reached_page_price_match", "allowed_urls": ["article1.html"], "expected_prices": ["$42"]},
                {**ranchor, "final_url": srv.url("article1.html"), "final_result": "price $89"},
            )
            check("reached_page_price_match mismatch", not r["ok"])

            r = await v._check(
                {"type": "structured_field_match", "expected": [{"name": "Lumina Lamp", "price": "$42"}]},
                {"done": True, "final_result": '[{"name": "Lumina Lamp", "price": "$42", "feature": "dimmable"}]'},
            )
            check("structured_field_match", r["ok"])

            r = await v._check(
                {"type": "multi_url_reached", "urls": ["article1.html", "article3.html"]},
                {"done": True, "visited_urls": [srv.url("article1.html"), srv.url("research.html"), srv.url("article3.html")]},
            )
            check("multi_url_reached", r["ok"])

            r = await v._check({"type": "dom_value_equals", "expr": "document.querySelector('#confirmation')?.textContent", "expected": "SUBMISSION_OK"},
                               {"done": True, "final_url": srv.url("confirm.html")})
            check("dom_value_equals", r["ok"] is True and bool(r["actual"]))

            t4 = TASKS_BY_ID["T4"]
            rec4 = {
                "done": True,
                "final_url": srv.url("dest.html"),
                "observed_title": "Benchmark Destination",
                "final_result": "Benchmark Destination at you have arrived",
                "visited_urls": [srv.url("dest.html")],
                "trace": [{"url": srv.url("start.html"), "actions": [{"click": {}}, {"navigate": {"url": srv.url("dest.html")}}], "result": []}],
            }
            full = await v.verify_task(t4, rec4)
            check("verify_task no-synthesis is None", full["synthesis_ok"] is None)
            check("verify_task T4 passes", full["destination_ok"] and full["data_ok"])
        finally:
            await v.close()


def test_report():
    print("report aggregation")
    with tempfile.TemporaryDirectory() as tmp:
        runs_dir = os.path.join(tmp, "runs")
        model_dir = os.path.join(runs_dir, "qwen3_1.7b")
        os.makedirs(model_dir, exist_ok=True)
        for i, (tid, cat) in enumerate([("T1", "success"), ("T1", "success"), ("T1", "extraction_failure"), ("T4", "success"), ("T4", "navigation_failure")]):
            rec = {
                "benchmark_version": "0.2",
                "task_id": tid,
                "tier": 1 if tid == "T1" else 2,
                "task_name": "x",
                "model": "qwen3:1.7b",
                "done": cat != "navigation_failure",
                "success": cat == "success",
                "verified": cat in ("success", "navigation_failure"),
                "destination_ok": cat != "navigation_failure",
                "data_ok": cat in ("success", "navigation_failure"),
                "synthesis_ok": None,
                "failure_category": cat,
                "steps": 3,
                "time_seconds": 120.0,
                "retries": 0,
                "recovery_events": 0,
                "retry_delay_total": 0,
            }
            with open(os.path.join(model_dir, f"run_x_{i:02d}.json"), "w", encoding="utf-8") as f:
                json.dump(rec, f)
        data = build_report_data(runs_dir, ["qwen3:1.7b"])
        model = data["models_detail"]["qwen3:1.7b"]
        check("report has 5 runs", model["runs"] == 5)
        check("tier map has both tiers", "1" in model["tiers"] and "2" in model["tiers"])
        check("overall rate", model["overall"]["success"] == 3)
        txt = format_report("qwen3:1.7b", model)
        check("report mentions tiers", "Tier 1" in txt and "Tier 2" in txt and "OVERALL" in txt)
        check("report shows categories", "extraction_failure" in txt and "navigation_failure" in txt)
        cmp = format_comparison(data)
        check("comparison table", "qwen3:1.7b" in cmp)


def test_tasks_registry():
    print("tasks registry")
    check("10 tasks", len(tasks_filter()) == 10)
    check("3 tiers", {t.tier for t in tasks_filter()} == {1, 2, 3})
    check("tier counts", [sum(1 for t in tasks_filter() if t.tier == k) for k in (1, 2, 3)] == [3, 3, 4])
    check("filter by tier", [t.task_id for t in tasks_filter(tiers=["1"])] == ["T1", "T2", "T3"])
    check("filter by task", [t.task_id for t in tasks_filter(task_ids=["T7", "T10"])] == ["T7", "T10"])


def test_harness_import():
    print("harness import (core intact)")
    try:
        import e2e_runner  # noqa: F401
        check("e2e_runner imports", True)
    except Exception as exc:
        check(f"e2e_runner imports: {exc}", False)


async def main_async():
    await test_fixture_server()
    test_extract_json()
    test_helpers()
    test_tasks_registry()
    test_classify()
    await test_verifier_checks()
    test_report()
    test_harness_import()


def main():
    asyncio.run(main_async())
    print()
    print(f"PASS {len(_PASS)}  FAIL {len(_FAIL)}")
    if _FAIL:
        print("FAILED:")
        for f in _FAIL:
            print("  -", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())