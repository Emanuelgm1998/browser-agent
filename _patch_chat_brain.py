from pathlib import Path

path = Path("chat_brain.py")
codigo = path.read_text(encoding="utf-8-sig")

# --- Cambio A: constantes ---
old_a = """# Numero de comprobaciones consecutivas con texto identico
# para considerar que el streaming termino.
STABLE_CHECKS = 3"""

new_a = """# Numero de comprobaciones consecutivas con texto identico
# para considerar que el streaming termino.
STABLE_CHECKS = 8

# Palabras que indican que el modelo todavia esta "pensando"/generando
# (observadas en la UI real de ChatGPT y Claude: "Pensar", "Triangulando").
# Si aparecen al final del texto, NO se considera respuesta estable
# aunque el texto no haya cambiado en varios polls.
GENERATING_MARKERS = [
    "Pensar",
    "Pensando",
    "Triangulando",
    "Analizando",
    "Razonando",
    "Thinking",
]"""

# --- Cambio B: fix en _wait_claude_response (patron original, sin marcador nuevo) ---
old_b = """            if current != before and stable >= STABLE_CHECKS:
                return self._extract_latest_response(
                    current,
                    before,
                )

        return self._extract_latest_response(
            last,
            before,
        )

    # --------------------------------------------------------
    # RESPONSE EXTRACTION
    # --------------------------------------------------------"""

new_b = """            if (
                current != before
                and stable >= STABLE_CHECKS
                and not self._looks_like_still_generating(current)
            ):
                return self._extract_latest_response(
                    current,
                    before,
                )

        return self._extract_latest_response(
            last,
            before,
        )

    # --------------------------------------------------------
    # DETECCION DE "SIGUE GENERANDO"
    # --------------------------------------------------------

    def _looks_like_still_generating(self, text: str) -> bool:
        \"\"\"
        Heuristica: si alguna palabra de GENERATING_MARKERS aparece
        cerca del final del texto visible, asumimos que el modelo
        todavia esta en estado de "pensando" y NO se debe considerar
        la respuesta como estable/completa todavia.
        \"\"\"
        if not text:
            return False
        cola = text[-300:]
        return any(marker in cola for marker in GENERATING_MARKERS)

    # --------------------------------------------------------
    # RESPONSE EXTRACTION
    # --------------------------------------------------------"""

# --- Cambio C: fix en _wait_chatgpt_response ---
old_c = """            # Evitar considerar inmediatamente el body viejo.
            if current != before and stable >= STABLE_CHECKS:
                return self._extract_latest_response(
                    current,
                    before,
                )

        return self._extract_latest_response(
            last,
            before,
        )

    # --------------------------------------------------------
    # CLAUDE
    # --------------------------------------------------------"""

new_c = """            # Evitar considerar inmediatamente el body viejo.
            # Tambien evitar cortar mientras el modelo sigue "pensando".
            if (
                current != before
                and stable >= STABLE_CHECKS
                and not self._looks_like_still_generating(current)
            ):
                return self._extract_latest_response(
                    current,
                    before,
                )

        return self._extract_latest_response(
            last,
            before,
        )

    # --------------------------------------------------------
    # CLAUDE
    # --------------------------------------------------------"""

cambios = [
    ("A (constantes)", old_a, new_a),
    ("B (claude response + helper)", old_b, new_b),
    ("C (chatgpt response)", old_c, new_c),
]

for nombre, old, new in cambios:
    count = codigo.count(old)
    if count != 1:
        raise SystemExit(
            f"ERROR en cambio {nombre}: el patron aparece {count} veces "
            f"(se esperaba exactamente 1). Abortando SIN modificar el archivo."
        )
    codigo = codigo.replace(old, new)

path.write_text(codigo, encoding="utf-8")
print("Parche aplicado correctamente a chat_brain.py")
