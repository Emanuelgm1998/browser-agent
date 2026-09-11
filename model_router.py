import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional


DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_FALLBACKS = ["qwen3:8b", "qwen3:4b", "qwen3:1.7b"]

TRANSIENT_ERROR_MARKERS = (
    "429",
    "quota",
    "rate limit",
    "resource exhausted",
    "temporarily unavailable",
    "timeout",
    "503",
    "500",
    "connection",
    "unavailable",
    "internal",
)


@dataclass
class RouterResult:
    text: str
    provider: str
    model: str
    raw: Any = None
    error: Optional[str] = None
    duration_s: float = 0.0
    try_order: list = field(default_factory=list)
    attempts: list = field(default_factory=list)


def clean_json(raw: str) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    try:
        json.loads(raw)
        return raw
    except Exception:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        candidate = match.group(0)
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            pass
    candidate = raw
    for _ in range(8):
        candidate = re.sub(r",\s*}(?=\s*$)", "}", candidate)
        if candidate.count("{") > candidate.count("}"):
            candidate += "}"
        try:
            return json.dumps(json.loads(candidate))
        except Exception:
            continue
    return None


def is_transient_error(error: str) -> bool:
    error = (error or "").lower()
    return any(marker in error for marker in TRANSIENT_ERROR_MARKERS)


class ModelRouter:
    """Router de modelos: Gemini primario -> Qwen3/Ollama fallback.

    Arquitectura objetivo:
        MODEL ROUTER
          ├── Gemini (si GOOGLE_API_KEY / GEMINI_API_KEY presente)
          │    └── ante 429 / quota / fallo transitorio
          │        └── Qwen3 / Ollama (qwen3:8b -> 4b -> 1.7b)

    Es aditivo: no toca e2e_runner ni el adaptador Qwen3ChatOllama.
    """

    def __init__(
        self,
        primary_model: str = DEFAULT_GEMINI_MODEL,
        ollama_model: str = DEFAULT_OLLAMA_MODEL,
        ollama_fallbacks: Optional[list] = None,
        ollama_host: str = DEFAULT_OLLAMA_HOST,
        gemini_api_key: Optional[str] = None,
    ) -> None:
        self.primary_model = primary_model
        self.ollama_model = ollama_model
        self.ollama_fallbacks = list(ollama_fallbacks or OLLAMA_FALLBACKS)
        if ollama_model not in self.ollama_fallbacks:
            self.ollama_fallbacks = [ollama_model] + self.ollama_fallbacks
        self.ollama_host = ollama_host
        self.gemini_api_key = gemini_api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.last_route: Optional[dataclass] = None
        self.metrics = {"calls": 0, "failures": 0, "by_provider": {}}

    # ------------------------------------------------------------------
    # Descubrimiento / estado
    # ------------------------------------------------------------------
    @property
    def gemini_available(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def ollama_available(self) -> bool:
        try:
            from ollama import Client

            client = Client(host=self.ollama_host)
            tags = client.list()
            models = []
            for m in tags.get("models", []):
                name = getattr(m, "model", None) or (m.get("model") if hasattr(m, "get") else None)
                if name:
                    models.append(name)
            return len(models) > 0
        except Exception:
            return False

    def available_providers(self) -> dict:
        return {
            "gemini": {"available": self.gemini_available, "model": self.primary_model},
            "ollama": {"available": self.ollama_available, "models": self.ollama_fallbacks},
        }

    # ------------------------------------------------------------------
    # Clientes privados
    # ------------------------------------------------------------------
    def _call_gemini(
        self,
        prompt: str,
        system_prompt: Optional[str],
        schema: Optional[dict],
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, Any]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.gemini_api_key)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        if schema is not None:
            config.response_mime_type = "application/json"
            config.response_schema = schema
        response = client.models.generate_content(
            model=self.primary_model,
            contents=prompt,
            config=config,
        )
        return (response.text or "").strip(), response

    def _call_ollama(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str],
        schema: Optional[dict],
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, Any]:
        from ollama import Client

        client = Client(host=self.ollama_host)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        kwargs = dict(
            model=model,
            messages=messages,
            think=False,
            options={
                "temperature": temperature,
                "num_ctx": 8192,
                "num_predict": max_tokens,
            },
        )
        if schema is not None:
            kwargs["format"] = schema
        response = client.chat(**kwargs)
        content = response.message.content or ""
        if schema is not None:
            repaired = clean_json(content)
            if repaired is None:
                raise ValueError(f"JSON invalid after repair: {content[:200]}")
            return repaired, response
        return content.strip(), response

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        schema: Optional[dict] = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> RouterResult:
        start = time.time()
        self.metrics["calls"] += 1
        try_order: list[str] = []
        attempts: list[dict] = []

        if self.gemini_available:
            try_order.append("gemini")
        for model in self.ollama_fallbacks:
            try_order.append(f"ollama/{model}")

        providers_first = ["gemini"] if self.gemini_available else []
        ollama_models_order = list(dict.fromkeys([self.ollama_model] + self.ollama_fallbacks))

        failure_log = []

        if "gemini" in providers_first:
            try:
                text, raw = self._call_gemini(
                    prompt, system_prompt, schema, temperature, max_tokens
                )
                return self._done("gemini", self.primary_model, text, raw, start)
            except Exception as exc:
                err = f"gemini/{self.primary_model}: {type(exc).__name__}: {exc}"
                attempts.append({"provider": "gemini", "model": self.primary_model, "error": err})
                failure_log.append(err)

        for model in ollama_models_order:
            try:
                text, raw = self._call_ollama(
                    prompt, model, system_prompt, schema, temperature, max_tokens
                )
                return self._done("ollama", model, text, raw, start, attempts, try_order)
            except Exception as exc:
                err = f"ollama/{model}: {type(exc).__name__}: {exc}"
                attempts.append({"provider": "ollama", "model": model, "error": err})
                failure_log.append(err)

        elapsed = round(time.time() - start, 3)
        self.metrics["failures"] += 1
        result = RouterResult(
            text="",
            provider="none",
            model="",
            error="; ".join(failure_log) or "no providers available",
            duration_s=elapsed,
            try_order=try_order,
            attempts=attempts,
        )
        self.last_route = result
        return result

    def _done(self, provider, model, text, raw, start, attempts=None, try_order=None) -> RouterResult:
        elapsed = round(time.time() - start, 3)
        result = RouterResult(
            text=text,
            provider=provider,
            model=model,
            raw=raw,
            duration_s=elapsed,
            try_order=list(try_order or []),
            attempts=list(attempts or []),
        )
        self.last_route = result
        self.metrics["by_provider"][provider] = self.metrics["by_provider"].get(provider, 0) + 1
        return result

    def __repr__(self) -> str:
        return (
            f"ModelRouter(gemini={self.gemini_available}, "
            f"ollama={self.ollama_fallbacks})"
        )