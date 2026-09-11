"""Self-test de model_router (sin pytest).

Uso:
    .venv\\Scripts\\python.exe test_model_router.py

Verifica:
1. Descubrimiento de proveedores (gemini requiere key).
2. Llamada real a Ollama (qwen3) -> texto.
3. Modo schema -> JSON válido.
4. Ruta sin proveedores -> error controlado.
"""

import json
import sys

from model_router import ModelRouter

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


def main():
    router = ModelRouter()

    print("== ModelRouter smoke ==")
    print("providers:", router.available_providers())
    check("router instanciado", router is not None)
    check("paleta ollama incluye qwen3:8b", "qwen3:8b" in router.ollama_fallbacks)
    if router.gemini_available:
        print("  gemini available (API key presente), probara primario")
    else:
        print("  gemini NO disponible (sin API key) -> fallback Ollama")

    print("\n== generate_text (modo texto) ==")
    res = router.generate_text("Reply with exactly: ok", max_tokens=64)
    print(f"  route={res.provider}/{res.model} dur={res.duration_s}s")
    print(f"  text={res.text[:120]!r}")
    check("ollama responde", res.provider == "ollama" and len(res.text) > 0)
    check("texto contiene ok", "ok" in res.text.lower())

    print("\n== generate_text (modo schema JSON) ==")
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    }
    res = router.generate_text(
        'Return JSON: {"title": "Example Domain"}', schema=schema, max_tokens=128
    )
    print(f"  route={res.provider}/{res.model}")
    parsed = None
    try:
        parsed = json.loads(res.text)
    except Exception as exc:
        check("schema: JSON valido", False, str(exc))
    if parsed is not None:
        check("schema: JSON valido", True, str(parsed)[:80])
        check("schema: tiene campo title", "title" in parsed)
        check("schema: title correcto", parsed.get("title") == "Example Domain")

    print("\n== ruta sin proveedores ==")
    empty = ModelRouter(gemini_api_key="", ollama_host="http://127.0.0.1:1")
    res = empty.generate_text("ping", max_tokens=16)
    print(f"  provider={res.provider} error={res.error[:100]!r}")
    check("error controlado", res.provider == "none" and bool(res.error))

    print("\n== fallback Gemini -> Ollama (gemini simulado fallido) ==")
    class FlakyGemini(ModelRouter):
        def _call_gemini(self, prompt, system_prompt, schema, temperature, max_tokens):
            raise RuntimeError("gemini 429 quota exceeded")

    flaky = FlakyGemini(gemini_api_key="FAKE-KEY")
    check("gemini marcado disponible", flaky.gemini_available)
    res = flaky.generate_text("Reply with exactly: ok", max_tokens=64)
    print(f"  provider={res.provider}/{res.model} error={res.error!r}")
    check("cayo a ollama", res.provider == "ollama" and "ok" in res.text.lower())
    check("registro el fallo de gemini", any(a["provider"] == "gemini" for a in res.attempts))

    print()
    if FAILURES:
        print(f"RESULTADO: {len(FAILURES)} fallo(s): {FAILURES}")
        sys.exit(1)
    print("RESULTADO: OK")


if __name__ == "__main__":
    main()