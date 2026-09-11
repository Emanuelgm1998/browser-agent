from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx


BASE_DIR = Path(__file__).resolve().parent
VISION_LOG_DIR = BASE_DIR / "logs" / "vision"
VISION_LOG_DIR.mkdir(parents=True, exist_ok=True)

OLLAMA_HOST = "http://127.0.0.1:11434"
VISION_MODEL = "qwen3-vl:4b"


class VisionResult:
    def __init__(
        self,
        state: str,
        confidence: float = 0.0,
        explanation: str = "",
        raw: str = "",
    ):
        self.state = state
        self.confidence = confidence
        self.explanation = explanation
        self.raw = raw

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "raw": self.raw,
        }


def _save_log(data: dict[str, Any]) -> None:
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    path = VISION_LOG_DIR / f"vision_{timestamp}_{time.time_ns()}.json"

    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _build_prompt() -> str:
    return """
You are the visual perception module of an autonomous browser agent.

Analyze the supplied browser screenshot.

Your job is ONLY to classify the visible page.

Return ONLY valid JSON with exactly these fields:

{
  "state": "NORMAL_PAGE",
  "confidence": 0.0,
  "explanation": "short explanation"
}

Allowed states:

NORMAL_PAGE
PRODUCT_PAGE
SEARCH_RESULTS
LOGIN_REQUIRED
CAPTCHA_DETECTED
HUMAN_VERIFICATION
ERROR_PAGE
UNKNOWN

Important:

- CAPTCHA_DETECTED means a CAPTCHA or anti-automation challenge is visibly present.
- HUMAN_VERIFICATION means the page asks the user to prove they are human.
- Do NOT attempt to solve or bypass any CAPTCHA.
- Do NOT provide CAPTCHA answers.
- Do NOT invent information that is not visible.
- If uncertain, use UNKNOWN.
- confidence must be between 0 and 1.
""".strip()


async def analyze_screenshot(
    screenshot_path: str | Path,
    model: str = VISION_MODEL,
    host: str = OLLAMA_HOST,
) -> VisionResult:

    screenshot_path = Path(screenshot_path)

    if not screenshot_path.exists():
        raise FileNotFoundError(
            f"Screenshot no encontrado: {screenshot_path}"
        )

    image_bytes = screenshot_path.read_bytes()

    prompt = _build_prompt()

    payload = {
        "model": model,
        "prompt": prompt,
        "images": [
            image_bytes.hex()
        ],
        "stream": False,
        "options": {
            "temperature": 0,
        },
    }

    started = time.perf_counter()

    async with httpx.AsyncClient(
        base_url=host,
        timeout=120.0,
    ) as client:

        response = await client.post(
            "/api/generate",
            json=payload,
        )

        response.raise_for_status()

        data = response.json()

    elapsed = time.perf_counter() - started

    raw = str(data.get("response", "")).strip()

    state = "UNKNOWN"
    confidence = 0.0
    explanation = ""

    try:
        parsed = json.loads(raw)

        state = str(
            parsed.get(
                "state",
                "UNKNOWN",
            )
        )

        confidence = float(
            parsed.get(
                "confidence",
                0.0,
            )
        )

        explanation = str(
            parsed.get(
                "explanation",
                "",
            )
        )

    except Exception:
        # Fallback conservador.
        lowered = raw.lower()

        if (
            "captcha" in lowered
            or "recaptcha" in lowered
            or "verify you are human" in lowered
            or "verifica que eres humano" in lowered
        ):
            state = "CAPTCHA_DETECTED"
            confidence = 0.75
            explanation = (
                "La respuesta visual contiene señales de "
                "verificación humana."
            )

    result = VisionResult(
        state=state,
        confidence=max(
            0.0,
            min(
                1.0,
                confidence,
            ),
        ),
        explanation=explanation,
        raw=raw,
    )

    _save_log(
        {
            "timestamp": time.time(),
            "model": model,
            "screenshot": str(screenshot_path),
            "elapsed_s": elapsed,
            "result": result.to_dict(),
        }
    )

    return result


def needs_human_intervention(
    result: VisionResult,
) -> bool:

    return result.state in {
        "CAPTCHA_DETECTED",
        "HUMAN_VERIFICATION",
        "LOGIN_REQUIRED",
    }
