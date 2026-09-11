"""Objective verification engine for benchmark v0.2.

Verification is done by an INDEPENDENT browser (its own Playwright/Chromium),
separate from the agent's browser. Checks compare observable facts
(URL, title, DOM, page text, structured output) against expected values
declared in the task. An "the LLM said it finished" claim is never used as
evidence by itself.

A chain is enforced:

    AGENT -> EXECUTION -> REAL BROWSER STATE -> VERIFIER -> PASS/FAIL
"""
import json
import re
from urllib.parse import urlparse

_WS = re.compile(r"\s+")
_PRICE = re.compile(r"\$\s?\d+")
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def norm(s):
    s = s or ""
    return _WS.sub(" ", str(s)).strip()


def norm_lower(s):
    return norm(s).lower()


def path_of(url):
    if not url:
        return None
    p = urlparse(url).path or "/"
    if p == "/":
        return "/"
    return p.rstrip("/") or "/"


def _norm_url(url):
    if not url:
        return None
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.port:
        host += f":{u.port}"
    scheme = (u.scheme or "http").lower()
    return f"{scheme}://{host}{u.path.rstrip('/') or '/'}"


def extract_json(text):
    """Best-effort parse of a JSON object/array embedded in LLM output."""
    if not text:
        return None
    cleaned = _FENCE.sub(r"\1", text).strip()
    candidates = [cleaned, text]
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, (dict, list)):
                return obj
        except Exception:
            pass
    for start_idx, opener in [(c.find("["), "["), (c.find("{"), "{")]:
        if start_idx == -1:
            continue
        closer = "}" if opener == "{" else "]"
        depth = 0
        for i in range(start_idx, len(candidates[0])):
            ch = candidates[0][i]
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(candidates[0][start_idx:i + 1])
                        return obj
                    except Exception:
                        break
    return None


def parse_trace(trace):
    """Reduce the harness trace into compact, comparable facts."""
    action_counts = {}
    visited = []
    nav_count = 0
    nav_retries = 0
    for entry in trace or []:
        url = (entry or {}).get("url")
        if url and url not in ("about:blank", ""):
            visited.append(url)
        for act in (entry or {}).get("actions") or []:
            if not isinstance(act, dict):
                continue
            name = next(iter(act), str(act))
            action_counts[name] = action_counts.get(name, 0) + 1
            if name == "navigate":
                nav_count += 1
        for res in (entry or {}).get("result") or []:
            if isinstance(res, dict):
                content = ""
                if isinstance(res.get("error"), str):
                    content = res["error"]
                elif isinstance(res.get("extracted_content"), str):
                    content = res["extracted_content"]
                low = content.lower()
                if "already on" in low or "blocked:" in low or "critical error" in low:
                    nav_retries += 1
    return {
        "action_counts": action_counts,
        "visited": visited,
        "nav_count": nav_count,
        "nav_retries": nav_retries,
    }


class Verifier:
    def __init__(self, headless=True):
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None

    async def _ensure(self):
        if self._browser is not None:
            return
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context()

    async def close(self):
        for closer in (self._context, self._browser, self._pw):
            try:
                if closer is not None:
                    await closer.close()
            except Exception:
                pass
        self._context = self._browser = self._pw = None

    async def _fetch(self, url):
        await self._ensure()
        page = await self._context.new_page()
        try:
            status = None
            try:
                resp = await page.goto(url, timeout=20000, wait_until="domcontentloaded")
                status = resp.status if resp is not None else None
            except Exception:
                pass
            title = ""
            text = ""
            try:
                title = await page.title() or ""
            except Exception:
                pass
            try:
                text = await page.evaluate("document.body ? document.body.innerText : ''")
            except Exception:
                pass
            return {"url": page.url, "title": title.strip(), "text": text, "status": status}
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _count_selector(self, url, selector):
        await self._ensure()
        page = await self._context.new_page()
        try:
            try:
                await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            except Exception:
                pass
            try:
                return await page.locator(selector).count()
            except Exception:
                return 0
        finally:
            try:
                await page.close()
            except Exception:
                pass

    def _record_url(self, record):
        url = record.get("final_url") or record.get("observed_url")
        if not url:
            visited = (record.get("visited_urls") or [])
            url = visited[-1] if visited else None
        return url

    async def _check(self, check, record):
        ctype = check["type"]
        target = self._record_url(record)
        if ctype == "title_exact":
            expected = check["expected"]
            actual = record.get("observed_title")
            if not actual and target:
                fetched = await self._fetch(target)
                actual = fetched["title"]
            ok = bool(actual) and norm(actual) == norm(expected)
            return {"ok": ok, "expected": expected, "actual": actual or None}
        if ctype == "final_url_equals":
            expected = check["expected"]
            mode = check.get("mode", "exact")
            actual_url = target
            if not actual_url:
                return {"ok": False, "expected": expected, "actual": None, "note": "no target url (agent no llego a ninguna URL)"}
            local = norm_lower(_norm_url(expected)) if expected else None
            actual = norm_lower(_norm_url(actual_url)) if actual_url else None
            if mode == "suffix":
                exp_path = path_of(expected)
                act_path = path_of(actual_url) or "/"
                ok = bool(exp_path) and act_path.endswith(exp_path) and exp_path in act_path
            else:
                ok = actual is not None and actual == local
            return {"ok": ok, "expected": expected, "actual": actual_url}
        if ctype == "final_url_in":
            allowed = check["allowed"]
            act_path = path_of(target) or ""
            ok = any(act_path.startswith("/" + a.rstrip("/")) or act_path.endswith("/" + a.rstrip("/")) for a in allowed)
            return {"ok": ok, "expected": allowed, "actual": target}
        if ctype == "text_contains":
            where = check.get("where", "final")
            expected = check["expected"]
            if isinstance(expected, str):
                expected = [expected]
            hay_final = norm_lower(record.get("final_result") or "")
            hay_reason = hay_final
            parsed = extract_json(record.get("final_result"))
            if isinstance(parsed, dict) and isinstance(parsed.get("reason"), str):
                hay_reason = norm_lower(parsed["reason"])
            hay_dom = ""
            if where in ("dom", "both", "either_final_or_dom") and target:
                try:
                    hay_dom = norm_lower((await self._fetch(target))["text"])
                except Exception:
                    hay_dom = ""
            present = []
            for needle in expected:
                n = norm_lower(needle)
                if where == "final":
                    hit = n in hay_final
                elif where == "dom":
                    hit = n in hay_dom
                elif where == "both":
                    hit = n in hay_final and n in hay_dom
                elif where == "reason_or_final":
                    hit = n in hay_reason or n in hay_final
                else:  # either_final_or_dom
                    hit = n in hay_final or n in hay_dom
                present.append(hit)
            ok = all(present)
            return {
                "ok": ok,
                "expected": expected,
                "actual": {
                    "final": (record.get("final_result") or "")[:300],
                    "dom_found": where in ("dom", "both") or None,
                },
                "note": "missing=" + ",".join(e for e, p in zip(expected, present) if not p),
            }
        if ctype == "element_exists":
            selector = check["selector"]
            count = 0
            if target:
                count = await self._count_selector(target, selector)
            ok = target is not None and count > 0
            return {"ok": ok, "expected": selector, "actual": count}
        if ctype == "dom_value_equals":
            expr = check["expr"]
            expected = check["expected"]
            actual = None
            ok = False
            if target:
                await self._ensure()
                page = await self._context.new_page()
                try:
                    await page.goto(target, timeout=20000, wait_until="domcontentloaded")
                    actual = await page.evaluate(expr)
                except Exception:
                    actual = None
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass
                if actual is not None:
                    ok = norm_lower(str(actual)) == norm_lower(str(expected))
            return {"ok": ok, "expected": expected, "actual": actual}
        if ctype == "source_data_match":
            expected = check["expected"]
            if isinstance(expected, str):
                expected = [expected]
            ok = False
            actual = None
            if target:
                try:
                    page_text = norm_lower((await self._fetch(target))["text"])
                    ok = all(norm_lower(e) in page_text for e in expected)
                    actual = page_text[:200]
                except Exception as exc:
                    actual = f"fetch_error: {type(exc).__name__}"
            return {"ok": ok, "expected": expected, "actual": actual}
        if ctype == "page_data_match":
            page = check["page"]
            tokens = check["tokens"]
            if isinstance(tokens, str):
                tokens = [tokens]
            visited_urls = []
            for v in (record.get("visited_urls") or []):
                p = path_of(v)
                if p:
                    visited_urls.append((v, p))
            suffix = "/" + page.rstrip("/")
            reached_candidates = [(u, p) for u, p in visited_urls if p.endswith(suffix)]
            reached = bool(reached_candidates)
            match = None
            note = None
            if reached:
                try:
                    page_text = norm_lower((await self._fetch(reached_candidates[-1][0]))["text"])
                    match = all(norm_lower(t) in page_text for t in tokens)
                    if not match:
                        missing = [t for t in tokens if norm_lower(t) not in page_text]
                        note = "missing=" + ",".join(missing)
                except Exception as exc:
                    match = None
                    note = f"fetch_error: {type(exc).__name__}"
            return {
                "ok": reached and match is True,
                "reached": reached,
                "match": match,
                "expected": tokens,
                "actual": {"page": page, "visited": reached, "match": match},
                "note": note,
            }
        if ctype == "structured_field_match":
            expected = check["expected"]
            parsed = extract_json(record.get("final_result"))
            actual = parsed
            missing = []
            if isinstance(parsed, list):
                for item in expected:
                    found = _find_matching_item(parsed, item)
                    if not found:
                        missing.append(item)
            elif isinstance(parsed, dict):
                for item in expected:
                    if not _dict_contains(parsed, item):
                        missing.append(item)
            else:
                missing = expected
            ok = not missing
            return {
                "ok": ok,
                "expected": expected,
                "actual": (json.dumps(parsed, ensure_ascii=False) if parsed is not None else None)[:400],
                "note": f"missing={json.dumps(missing, ensure_ascii=False)}" if missing else None,
            }
        if ctype == "multi_url_reached":
            urls = check["urls"]
            visited = [path_of(u) for u in (record.get("visited_urls") or []) if path_of(u)]
            missing = [u for u in urls if not any(v.endswith(u) for v in visited)]
            ok = not missing
            return {"ok": ok, "expected": urls, "actual": visited, "note": f"missing={missing}" if missing else None}
        if ctype == "reached_page_price_match":
            allowed = check["allowed_urls"]
            expected_prices = check["expected_prices"]
            act_path = path_of(target) or "/"
            if not any(act_path.endswith("/" + a.rstrip("/")) for a in allowed):
                return {"ok": False, "expected": f"reached in {allowed}", "actual": act_path, "note": "wrong destination"}
            page_price = None
            if target:
                try:
                    page_text = (await self._fetch(target))["text"]
                    m = _PRICE.search(page_text)
                    page_price = m.group(0).replace(" ", "") if m else None
                except Exception:
                    page_price = None
            if page_price not in expected_prices:
                return {"ok": False, "expected": expected_prices, "actual": page_price, "note": "price not in allowed set"}
            final = norm_lower(record.get("final_result") or "")
            ok = norm_lower(page_price) in final
            return {"ok": ok, "expected": page_price, "actual": final[:200]}
        return {"ok": False, "expected": check, "actual": None, "note": f"unknown check type: {ctype}"}

    async def verify_task(self, task, record):
        groups = {}
        gate_map = {
            "destination": task.destination,
            "data": task.data,
            "synthesis": task.synthesis,
        }
        for name, checks in gate_map.items():
            results = []
            for c in checks:
                try:
                    r = await self._check(c, record)
                except Exception as exc:
                    r = {"ok": False, "expected": c, "actual": None, "note": f"verifier_error: {type(exc).__name__}: {exc}"}
                r["check"] = c
                results.append(r)
            groups[name] = results
        ok = {name: all(r["ok"] for r in results) for name, results in groups.items()}
        ok["synthesis"] = ok["synthesis"] if task.synthesis else None
        pages = {}
        for name, results in groups.items():
            for r in results:
                c = r.get("check") or {}
                if c.get("type") == "page_data_match":
                    pages[c["page"]] = {"reached": r.get("reached"), "match": r.get("match")}
        return {
            "groups": groups,
            "destination_ok": ok["destination"],
            "data_ok": ok["data"],
            "synthesis_ok": ok["synthesis"],
            "pages": pages,
        }


def _find_matching_item(items, expected):
    for it in items:
        if isinstance(it, dict) and _dict_contains(it, expected):
            return it
    return None


def _dict_contains(d, expected):
    for k, v in expected.items():
        actual = d.get(k)
        if actual is None:
            return False
        if norm_lower(v) not in norm_lower(str(actual)):
            return False
    return True


# ---------------------------------------------------------------------------
# Failure classification (Fase 9)
# ---------------------------------------------------------------------------

_INFRA_MARKERS = (
    "timeout",
    "timed out",
    "connectionerror",
    "conn refused",
    "modelprovidererror",
    "ollama",
    "runtimeerror",
    "exception",
    "browser error",
    "navigationwatchdog",
)
_EXTERNAL_MARKERS = ("net::", "dns", "err_", "neterr", "err_name_not_resolved", "err_connection", "404", "502", "503")


def classify_failure(task, record, checks):
    timeout = bool(record.get("timeout"))
    if timeout:
        return "timeout"

    done = bool(record.get("done"))
    if not done:
        errors = " ".join(e or "" for e in (record.get("errors") or [])).lower()
        if task.depends_on_external and any(m in errors for m in _EXTERNAL_MARKERS):
            return "external_site_failure"
        if any(m in errors for m in _INFRA_MARKERS):
            return "infrastructure_failure"
        if record.get("steps", 0) >= record.get("max_steps", 10**9):
            return "max_steps"
        if errors:
            return "run_error"
        return "model_output_failure"

    dest_ok = bool(checks["destination_ok"])
    data_ok = bool(checks["data_ok"])
    synth_ok = bool(checks["synthesis_ok"])

    needs_synth = "synthesis" in task.success_gates
    counts = parse_trace(record.get("trace"))["action_counts"]
    interaction_ok = all(
        counts.get(action, 0) >= needed
        for action, needed in (task.interaction_required or {}).items()
    )
    passed = (
        dest_ok
        and data_ok
        and interaction_ok
        and (not needs_synth or synth_ok)
    )
    if passed:
        return "success"

    if not interaction_ok:
        missing = {
            a: {"needed": n, "seen": counts.get(a, 0)}
            for a, n in (task.interaction_required or {}).items()
            if counts.get(a, 0) < n
        }
        return "interaction_failure"

    if not dest_ok:
        if task.required_urls:
            visited = [path_of(u) for u in (record.get("visited_urls") or []) if path_of(u)]
            if not all(any(v.endswith(u.rstrip("/")) for v in visited) for u in task.required_urls):
                return "navigation_failure"
        return "navigation_failure"

    if not data_ok:
        return "extraction_failure"

    if needs_synth and not synth_ok:
        return "synthesis_failure"

    return "verification_failure"