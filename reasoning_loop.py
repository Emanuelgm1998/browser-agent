from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from chat_brain import ChatBrain


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

RESULT_DIR = LOG_DIR / "reasoning_results"
RESULT_DIR.mkdir(exist_ok=True)


# ============================================================
# PLAN MODELS
# ============================================================

class PlanStep(BaseModel):
    action: str
    description: str
    target: str | None = None


class FinalPlan(BaseModel):
    goal: str
    summary: str
    steps: list[PlanStep] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    requires_human_approval: bool = True


# ============================================================
# JSON EXTRACTION
# ============================================================

def extract_json(text: str) -> dict[str, Any]:
    """
    Intenta encontrar un objeto JSON dentro de una respuesta LLM.

    Soporta:
    - JSON puro
    - ```json ... ```
    - texto antes/después del JSON
    - objetos JSON balanceados
    """

    if not text:
        raise ValueError("La respuesta está vacía.")

    text = text.strip()

    # --------------------------------------------------------
    # 1. JSON directo
    # --------------------------------------------------------

    try:
        data = json.loads(text)

        if isinstance(data, dict):
            return data

    except (json.JSONDecodeError, TypeError):
        pass

    # --------------------------------------------------------
    # 2. Markdown code block
    # --------------------------------------------------------

    matches = re.findall(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    for candidate in matches:

        try:
            data = json.loads(candidate.strip())

            if isinstance(data, dict):
                return data

        except (json.JSONDecodeError, TypeError):
            continue

    # --------------------------------------------------------
    # 3. Buscar cualquier objeto JSON balanceado
    # --------------------------------------------------------

    start_positions = [
        match.start()
        for match in re.finditer(r"\{", text)
    ]

    for start in start_positions:

        depth = 0
        in_string = False
        escape = False

        for index in range(start, len(text)):

            char = text[index]

            if in_string:

                if escape:
                    escape = False

                elif char == "\\":
                    escape = True

                elif char == '"':
                    in_string = False

                continue

            if char == '"':
                in_string = True

            elif char == "{":
                depth += 1

            elif char == "}":

                depth -= 1

                if depth == 0:

                    candidate = text[start:index + 1]

                    try:
                        data = json.loads(candidate)

                        if isinstance(data, dict):
                            return data

                    except (json.JSONDecodeError, TypeError):
                        pass

                    break

    raise ValueError(
        "No se pudo extraer un JSON válido de la respuesta."
    )


# ============================================================
# VALIDATE PLAN
# ============================================================

def validate_plan(text: str) -> FinalPlan:

    data = extract_json(text)

    return FinalPlan.model_validate(data)


# ============================================================
# BRAIN CALL
# ============================================================

async def call_brain(
    brain: ChatBrain,
    method_name: str,
    prompt: str,
) -> str:

    method = getattr(brain, method_name)

    result = method(prompt)

    if asyncio.iscoroutine(result):
        result = await result

    if result is None:
        return ""

    if not isinstance(result, str):
        result = str(result)

    return result.strip()


# ============================================================
# SAVE TXT RESULT
# ============================================================

def save_result_txt(
    task: str,
    initial_plan: str,
    claude_review: str,
    final_response: str,
    validation_status: str,
    validation_error: str = "",
    validated_plan: FinalPlan | None = None,
) -> Path:

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    result_path = RESULT_DIR / (
        f"reasoning_result_{timestamp}.txt"
    )

    lines: list[str] = []

    lines.append("=" * 70)
    lines.append("BROWSER AGENT IA - DUAL BRAIN RESULT")
    lines.append("=" * 70)
    lines.append("")

    lines.append("TIMESTAMP")
    lines.append("-" * 70)
    lines.append(datetime.now().isoformat())
    lines.append("")

    lines.append("TASK")
    lines.append("-" * 70)
    lines.append(task)
    lines.append("")

    lines.append("=" * 70)
    lines.append("CHATGPT - INITIAL PLAN")
    lines.append("=" * 70)
    lines.append("")
    lines.append(initial_plan or "[SIN RESPUESTA]")
    lines.append("")

    lines.append("=" * 70)
    lines.append("CLAUDE - REVIEW")
    lines.append("=" * 70)
    lines.append("")
    lines.append(claude_review or "[SIN RESPUESTA]")
    lines.append("")

    lines.append("=" * 70)
    lines.append("CHATGPT - FINAL PLAN")
    lines.append("=" * 70)
    lines.append("")
    lines.append(final_response or "[SIN RESPUESTA]")
    lines.append("")

    lines.append("=" * 70)
    lines.append("JSON VALIDATION")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"STATUS: {validation_status}")
    lines.append("")

    if validation_error:
        lines.append("ERROR:")
        lines.append(validation_error)
        lines.append("")

    if validated_plan is not None:

        lines.append("=" * 70)
        lines.append("VALIDATED PLAN")
        lines.append("=" * 70)
        lines.append("")

        lines.append(
            json.dumps(
                validated_plan.model_dump(),
                indent=2,
                ensure_ascii=False,
            )
        )

        lines.append("")

    lines.append("=" * 70)
    lines.append("NEXT ACTION")
    lines.append("=" * 70)
    lines.append("")

    if validation_status == "VALID":

        lines.append(
            "El plan fue convertido correctamente a JSON "
            "y validado con Pydantic."
        )

        lines.append(
            "Puede pasar al Human Gate y posteriormente "
            "al executor."
        )

    else:

        lines.append(
            "El resultado completo fue guardado para "
            "revision manual."
        )

        lines.append(
            "No se ejecuto ningun navegador ni accion sensible."
        )

        lines.append(
            "El resultado puede entregarse manualmente "
            "al agente CLI."
        )

    lines.append("")
    lines.append("=" * 70)
    lines.append("END")
    lines.append("=" * 70)

    result_path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    return result_path


# ============================================================
# MAIN REASONING LOOP
# ============================================================

async def run_reasoning(task: str) -> FinalPlan | None:

    print("")
    print("=" * 70)
    print(" MVP1 DUAL BRAIN REASONING LOOP")
    print("=" * 70)
    print("")
    print("TAREA:")
    print(task)
    print("")

    initial_plan = ""
    claude_review = ""
    final_response = ""

    # --------------------------------------------------------
    # CHAT BRAIN
    # --------------------------------------------------------

    async with ChatBrain() as brain:

        # ====================================================
        # 1. CHATGPT
        # ====================================================

        print("=" * 70)
        print("[1/3] CHATGPT -> PLAN INICIAL")
        print("=" * 70)
        print("")

        chatgpt_prompt = f"""
You are the primary planning brain of an autonomous browser agent.

User task:

{task}

Create a clear execution plan for a browser agent.

Requirements:

- Break the task into concrete browser actions.
- Identify target URLs or pages when possible.
- Identify verification criteria.
- Identify risks or sensitive actions.
- Do not execute anything.
- Return a human-readable plan for Claude to review.
"""

        try:

            initial_plan = await call_brain(
                brain,
                "ask_chatgpt",
                chatgpt_prompt,
            )

        except Exception as exc:

            initial_plan = (
                "[ERROR CHATGPT]\n"
                + type(exc).__name__
                + ": "
                + str(exc)
            )

            print(initial_plan)
            print("")

        print("CHATGPT RESPONSE:")
        print("")
        print(initial_plan)
        print("")

        # ====================================================
        # 2. CLAUDE
        # ====================================================

        print("=" * 70)
        print("[2/3] CLAUDE -> REVIEW")
        print("=" * 70)
        print("")

        claude_prompt = f"""
You are the reviewing brain of an autonomous browser agent.

Original user task:

{task}

Initial plan generated by ChatGPT:

{initial_plan}

Review this plan.

Check:

- correctness
- missing steps
- browser navigation issues
- verification requirements
- dangerous or sensitive actions
- ambiguity

Return a concise review and concrete corrections.

Do not execute anything.
"""

        try:

            claude_review = await call_brain(
                brain,
                "ask_claude",
                claude_prompt,
            )

        except Exception as exc:

            claude_review = (
                "[ERROR CLAUDE]\n"
                + type(exc).__name__
                + ": "
                + str(exc)
            )

            print(claude_review)
            print("")

        print("CLAUDE RESPONSE:")
        print("")
        print(claude_review)
        print("")

        # ====================================================
        # 3. CHATGPT FINAL
        # ====================================================

        print("=" * 70)
        print("[3/3] CHATGPT -> PLAN FINAL")
        print("=" * 70)
        print("")

        final_prompt = f"""
You are the final planning brain for an autonomous browser agent.

User task:

{task}

Initial ChatGPT plan:

{initial_plan}

Claude review:

{claude_review}

Create the FINAL execution plan.

First provide the plan in a clear human-readable format.

Then, at the END, provide a machine-readable JSON object.

The JSON should use this schema:

{{
  "goal": "string",
  "summary": "string",
  "steps": [
    {{
      "action": "string",
      "description": "string",
      "target": "string or null"
    }}
  ],
  "risks": [
    "string"
  ],
  "requires_human_approval": true
}}

Rules:

- steps must contain at least one item.
- actions must be concrete browser actions.
- include verification steps.
- never invent credentials.
- never include passwords.
- never include API keys.
- never include cookies.
- never include security codes.
- never include secrets.
- sensitive actions must require human approval.
- do not execute anything.
"""

        try:

            final_response = await call_brain(
                brain,
                "ask_chatgpt",
                final_prompt,
            )

        except Exception as exc:

            final_response = (
                "[ERROR CHATGPT FINAL]\n"
                + type(exc).__name__
                + ": "
                + str(exc)
            )

        print("CHATGPT FINAL RESPONSE:")
        print("")
        print(final_response)
        print("")

    # ========================================================
    # VALIDATION
    # ========================================================

    print("=" * 70)
    print("[VALIDACION]")
    print("=" * 70)
    print("")

    validated_plan: FinalPlan | None = None
    validation_status = "FAILED"
    validation_error = ""

    try:

        validated_plan = validate_plan(final_response)

        validation_status = "VALID"

        print("OK: JSON encontrado.")
        print("OK: JSON valido.")
        print("OK: Pydantic valido.")
        print("")

    except Exception as exc:

        validation_status = "FAILED"

        validation_error = (
            type(exc).__name__
            + ": "
            + str(exc)
        )

        print("AVISO: el JSON no pudo validarse.")
        print("")
        print(validation_error)
        print("")
        print(
            "NO SE PIERDE EL RESULTADO."
        )
        print(
            "Se guardara todo en un archivo TXT."
        )
        print("")

    # ========================================================
    # SAVE COMPLETE RESULT
    # ========================================================

    result_path = save_result_txt(
        task=task,
        initial_plan=initial_plan,
        claude_review=claude_review,
        final_response=final_response,
        validation_status=validation_status,
        validation_error=validation_error,
        validated_plan=validated_plan,
    )

    print("=" * 70)
    print("RESULTADO GUARDADO")
    print("=" * 70)
    print("")
    print(result_path)
    print("")

    # ========================================================
    # IF JSON FAILED -> STOP SAFELY
    # ========================================================

    if validated_plan is None:

        print("=" * 70)
        print("MODO MANUAL")
        print("=" * 70)
        print("")
        print(
            "El plan no sera ejecutado."
        )
        print("")
        print(
            "Puedes abrir el archivo TXT y "
            "entregarlo manualmente al otro agente CLI."
        )
        print("")
        print(
            "NO se ejecutara Browser Use."
        )
        print(
            "NO se ejecutara Qwen3."
        )
        print(
            "NO se abrira Chromium para la tarea."
        )
        print("")

        return None

    # ========================================================
    # VALIDATED PLAN
    # ========================================================

    print("=" * 70)
    print("PLAN FINAL VALIDADO")
    print("=" * 70)
    print("")

    print(
        json.dumps(
            validated_plan.model_dump(),
            indent=2,
            ensure_ascii=False,
        )
    )

    print("")

    # ========================================================
    # HUMAN GATE
    # ========================================================

    if validated_plan.requires_human_approval:

        print("=" * 70)
        print("HUMAN GATE")
        print("=" * 70)
        print("")

        print(
            "El plan requiere aprobacion humana."
        )

        print("")

        while True:

            answer = input(
                "Aprobar este plan? [y/N]: "
            ).strip().lower()

            if answer in {
                "y",
                "yes",
                "s",
                "si",
                "sí",
            }:

                print("")
                print("PLAN APROBADO.")
                print("")
                break

            if answer in {
                "",
                "n",
                "no",
            }:

                print("")
                print("PLAN RECHAZADO.")
                print("")
                return validated_plan

            print(
                "Respuesta no valida. Usa y o N."
            )

    # ========================================================
    # NO EXECUTOR YET
    # ========================================================

    print("=" * 70)
    print("PLAN VALIDADO Y APROBADO")
    print("=" * 70)
    print("")

    print(
        "IMPORTANTE:"
    )

    print(
        "El executor Browser Agent todavia "
        "NO esta conectado a este reasoning loop."
    )

    print(
        "Esta fase termina aqui."
    )

    print("")

    return validated_plan


# ============================================================
# CLI
# ============================================================

def main() -> None:

    if len(sys.argv) < 2:

        print("")
        print("Uso:")
        print("")
        print(
            'python reasoning_loop.py "tu tarea"'
        )
        print("")

        sys.exit(1)

    task = " ".join(
        sys.argv[1:]
    ).strip()

    try:

        asyncio.run(
            run_reasoning(task)
        )

    except KeyboardInterrupt:

        print("")
        print(
            "Interrumpido por el usuario."
        )
        print("")

        sys.exit(130)

    except Exception as exc:

        print("")
        print("=" * 70)
        print("ERROR CRITICO EN REASONING LOOP")
        print("=" * 70)
        print("")

        print(
            type(exc).__name__
            + ": "
            + str(exc)
        )

        print("")

        sys.exit(1)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
