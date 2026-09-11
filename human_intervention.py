from __future__ import annotations

import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs" / "human_intervention"

LOG_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


def wait_for_human(
    reason: str,
    instruction: str,
) -> bool:

    timestamp = time.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    log_path = (
        LOG_DIR
        / f"intervention_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}.txt"
    )

    log_path.write_text(
        "\n".join(
            [
                "HUMAN INTERVENTION",
                "===================",
                f"Timestamp: {timestamp}",
                f"Reason: {reason}",
                f"Instruction: {instruction}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print()
    print("=" * 70)
    print("⚠️  INTERVENCIÓN HUMANA REQUERIDA")
    print("=" * 70)
    print()
    print(f"Motivo: {reason}")
    print()
    print(instruction)
    print()
    print("El agente está PAUSADO.")
    print("No intentará resolver ni evadir la protección.")
    print()
    print("Cuando hayas terminado, escribe:")
    print()
    print("  CONTINUAR")
    print()
    print("Para cancelar:")
    print()
    print("  CANCELAR")
    print()

    while True:

        try:
            answer = input("> ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            return False

        if answer == "CONTINUAR":
            print()
            print("Intervención humana confirmada.")
            print("Reanudando agente...")
            print()
            return True

        if answer == "CANCELAR":
            print()
            print("Ejecución cancelada por el usuario.")
            print()
            return False

        print(
            "Respuesta no válida. "
            "Escribe CONTINUAR o CANCELAR."
        )


def human_gate_for_state(state: str) -> bool:

    instructions = {
        "CAPTCHA_DETECTED": (
            "Resuelve manualmente la verificación que aparece "
            "en el navegador. No cierres la página."
        ),
        "HUMAN_VERIFICATION": (
            "Completa manualmente la verificación humana "
            "que aparece en el navegador."
        ),
        "LOGIN_REQUIRED": (
            "Realiza manualmente el inicio de sesión "
            "en el navegador."
        ),
    }

    instruction = instructions.get(
        state,
        "Revisa el navegador y completa manualmente "
        "la acción requerida.",
    )

    return wait_for_human(
        reason=state,
        instruction=instruction,
    )
