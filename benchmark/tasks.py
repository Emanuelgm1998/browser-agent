"""Benchmark v0.2 task definitions.

Each task declares its objective criteria up-front:
- destination checks: how the agent proves it reached the right place
- data checks: how the extracted content is validated
- synthesis checks (Tier 3): how the final conclusion is validated
- interaction_required: which actions must appear in the trace
- required_urls: which pages must have been visited

Adding a new task = adding one BenchTask entry. No core changes needed.
"""
from dataclasses import dataclass, field
from typing import Any


def title_exact(expected):
    return {"type": "title_exact", "expected": expected}


def url_suffix(expected):
    return {"type": "final_url_equals", "expected": expected, "mode": "suffix"}


def url_in(allowed):
    return {"type": "final_url_in", "allowed": allowed}


def text_contains(expected, where="final"):
    return {"type": "text_contains", "expected": expected, "where": where}


def element_exists(selector):
    return {"type": "element_exists", "selector": selector}


def source_data_match(expected):
    return {"type": "source_data_match", "expected": expected}


def structured_fields(expected_list):
    return {"type": "structured_field_match", "expected": expected_list}


@dataclass
class BenchTask:
    task_id: str
    tier: int
    name: str
    difficulty: str
    prompt: str  # may contain {base} -> replaced with fixture server URL
    destination: list
    data: list
    interaction_required: dict = field(default_factory=dict)  # {"click": 1, ...}
    required_urls: list = field(default_factory=list)
    synthesis: list = field(default_factory=list)
    success_gates: list = field(default_factory=lambda: ["destination", "data"])
    max_steps: int = 10
    timeout_s: int = 300
    model_allowed: list = field(default_factory=lambda: ["*"])
    depends_on_external: bool = False
    live_capable: bool = False


TASKS: list[BenchTask] = [
    # ============================ TIER 1 ============================
    BenchTask(
        task_id="T1",
        tier=1,
        name="Open URL + exact title",
        difficulty="simple",
        prompt=(
            "Open https://example.com, read the page, and report the EXACT page "
            "title and the current URL. Then stop."
        ),
        destination=[
            title_exact("Example Domain"),
            {"type": "final_url_equals", "expected": "https://example.com/", "mode": "suffix"},
        ],
        data=[
            text_contains("Example Domain"),
        ],
        max_steps=8,
        timeout_s=300,
        depends_on_external=True,
    ),
    BenchTask(
        task_id="T2",
        tier=1,
        name="Exact text extraction",
        difficulty="simple",
        prompt=(
            "Open {base}/anchor.html, read the page, and extract the exact "
            "sentence that contains the code #K7QZ4. Report the full sentence "
            "exactly as written. Then stop."
        ),
        destination=[
            title_exact("Anchor Page"),
            url_suffix("anchor.html"),
        ],
        data=[
            text_contains("#K7QZ4"),
            text_contains("turquoise"),
            source_data_match(["#K7QZ4"]),
        ],
        max_steps=8,
        timeout_s=300,
    ),
    BenchTask(
        task_id="T3",
        tier=1,
        name="Find information on page",
        difficulty="simple",
        prompt=(
            "Open {base}/anchor.html. Find the registry model id on the page "
            "(the table value next to the label 'Registry model id') and report "
            "ONLY that model id value. Then stop."
        ),
        destination=[
            title_exact("Anchor Page"),
            url_suffix("anchor.html"),
        ],
        data=[
            text_contains("4A9-MN72"),
            source_data_match(["4A9-MN72"]),
        ],
        max_steps=8,
        timeout_s=300,
    ),
    # ============================ TIER 2 ============================
    BenchTask(
        task_id="T4",
        tier=2,
        name="Click + destination verification",
        difficulty="moderate",
        prompt=(
            "Open {base}/start.html. Click the link labeled 'Go to destination'. "
            "After the navigation, report the final page title and URL exactly. "
            "Then stop."
        ),
        destination=[
            title_exact("Benchmark Destination"),
            url_suffix("dest.html"),
            element_exists("#arrived"),
        ],
        data=[
            text_contains("Benchmark Destination"),
            text_contains("You have arrived"),
        ],
        interaction_required={"click": 1},
        required_urls=["dest.html"],
        max_steps=10,
        timeout_s=360,
    ),
    BenchTask(
        task_id="T5",
        tier=2,
        name="Multi-step navigation",
        difficulty="moderate",
        prompt=(
            "Open {base}/s5_start.html. Click the link 'Step 2', then on the "
            "next page click 'Go to end'. Finally report the value shown in the "
            "element with id 'end_data' plus the final URL. Then stop."
        ),
        destination=[
            title_exact("End Page"),
            url_suffix("end.html"),
        ],
        data=[
            text_contains("END_DATA_77"),
            source_data_match(["END_DATA_77"]),
        ],
        interaction_required={"click": 2},
        required_urls=["end.html"],
        max_steps=12,
        timeout_s=420,
    ),
    BenchTask(
        task_id="T6",
        tier=2,
        name="Form interaction",
        difficulty="moderate",
        prompt=(
            "Open {base}/form.html. Fill the name field with 'Ada' and the email "
            "field with 'ada@example.com', then submit the form. Report the "
            "confirmation message and the final URL. Then stop."
        ),
        destination=[
            title_exact("Form Confirmed"),
            url_suffix("confirm.html"),
            element_exists("#confirmation"),
        ],
        data=[
            text_contains("SUBMISSION_OK"),
            source_data_match(["SUBMISSION_OK"]),
        ],
        interaction_required={"input": 1, "click": 1},
        required_urls=["confirm.html"],
        max_steps=12,
        timeout_s=420,
    ),
    # ============================ TIER 3 ============================
    BenchTask(
        task_id="T7",
        tier=3,
        name="Search + result extraction",
        difficulty="hard",
        prompt=(
            "Open {base}/search.html. Search for 'lumina' using the search box, "
            "open the first search result, and report the product price shown "
            "on the result page. Then stop."
        ),
        destination=[
            title_exact("Lumina Lamp"),
            url_suffix("article1.html"),
        ],
        data=[
            # data is validated against the page actually reached (see verifier)
            {"type": "text_contains", "expected": ["$42", "$89"], "where": "final"},
            {"type": "reached_page_price_match", "allowed_urls": ["article1.html", "article2.html"], "expected_prices": ["$42", "$89"]},
        ],
        interaction_required={"input": 1},
        max_steps=15,
        timeout_s=540,
        live_capable=True,
    ),
    BenchTask(
        task_id="T8",
        tier=3,
        name="Structured research",
        difficulty="hard",
        prompt=(
            "Open {base}/research.html. Open each of the three product links and "
            "for each product extract its name, price and feature. Return the "
            "results as a JSON array of objects with keys name, price, feature. "
            "Then stop."
        ),
        destination=[
            title_exact("Research Index"),
            {"type": "multi_url_reached", "urls": ["article1.html", "article2.html", "article3.html"]},
        ],
        data=[
            structured_fields(
                [
                    {"name": "Lumina Lamp", "price": "$42", "feature": "dimmable"},
                    {"name": "Lumina Desk", "price": "$89", "feature": "height-adjustable"},
                    {"name": "Lumina Strip", "price": "$25", "feature": "IP65 waterproof"},
                ]
            ),
            source_data_match(["Lumina Lamp", "Lumina Desk", "Lumina Strip"]),
        ],
        required_urls=["article1.html", "article2.html", "article3.html"],
        max_steps=18,
        timeout_s=600,
        live_capable=True,
    ),
    BenchTask(
        task_id="T9",
        tier=3,
        name="Comparison task",
        difficulty="hard",
        prompt=(
            "Open {base}/compare_index.html. Open both product pages AlphaGlow "
            "and BetaShine. From each page extract the price and the main feature "
            "(lumens). Then report which product is cheaper. Then stop."
        ),
        destination=[
            {"type": "multi_url_reached", "urls": ["compare_a.html", "compare_b.html"]},
        ],
        data=[
            {"type": "page_data_match", "page": "compare_a.html", "tokens": ["$99", "2000"]},
            {"type": "page_data_match", "page": "compare_b.html", "tokens": ["$79", "2200"]},
        ],
        synthesis=[
            text_contains("BetaShine"),
            text_contains("$79"),
            text_contains("$99"),
            text_contains("2000"),
            text_contains("2200"),
        ],
        required_urls=["compare_a.html", "compare_b.html"],
        success_gates=["destination", "data"],  # synthesis measured separately
        max_steps=18,
        timeout_s=600,
        live_capable=True,
    ),
    BenchTask(
        task_id="T10",
        tier=3,
        name="Multi-step web research",
        difficulty="advanced",
        prompt=(
            "Open {base}/research_index.html. Gather the facts about AlphaGlow "
            "and BetaShine (price and lumens) from the pages linked there, then "
            "decide which product is the better value. It must have at least "
            "2000 lumens and be cheaper. Return a JSON object with keys chosen, "
            "price, reason. Then stop."
        ),
        destination=[
            {"type": "multi_url_reached", "urls": ["compare_a.html", "compare_b.html"]},
        ],
        data=[
            structured_fields(
                [
                    {"chosen": "BetaShine", "price": "$79"},
                ]
            ),
            source_data_match(["$79", "BetaShine"]),
        ],
        synthesis=[
            text_contains("BetaShine"),
            {"type": "text_contains", "expected": ["lumens", "2000"], "where": "reason_or_final"},
        ],
        success_gates=["destination", "data", "synthesis"],
        required_urls=["compare_a.html", "compare_b.html"],
        max_steps=20,
        timeout_s=720,
        live_capable=True,
    ),
]

TASKS_BY_ID = {t.task_id: t for t in TASKS}
TIERS = sorted({t.tier for t in TASKS})


def tasks_filter(task_ids=None, tiers=None):
    tasks = TASKS
    if task_ids:
        tasks = [t for t in tasks if t.task_id in task_ids]
    if tiers:
        tasks = [t for t in tasks if str(t.tier) in {str(x) for x in tiers}]
    return tasks