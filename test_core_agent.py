"""Self-test del AgentCore (sin pytest).

Uso:
    .venv\\Scripts\\python.exe test_core_agent.py

Escenarios:
1. Herramienta calculate + verificacion numerica.
2. HIL: ask_user -> el agente pregunta y continua con la respuesta.
3. Aprobacion denegada en herramienta de riesgo.
4. Guardia de max_steps (verificador siempre falla).
"""

import sys

from core_agent import AgentCore, Tool, ToolRegistry, safe_eval
from model_router import ModelRouter

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


def main():
    print("== safe_eval ==")
    check("17*23 == 391", safe_eval("17*23") == 391)
    check("(2+3)*4 == 20", safe_eval("(2+3)*4") == 20)

    def make(verifier=None, human=None, max_steps=8):
        return AgentCore(
            router=ModelRouter(),
            registry=ToolRegistry(),
            max_steps=max_steps,
            verifier=verifier,
            human_feedback=human,
            verbose=False,
        )

    print("\n== Escenario 1: calculate + verificacion ==")
    agent = make(
        verifier=lambda task, answer, ctx: ("391" in answer, "se esperaba 391")
    )
    res = agent.run("Calcula 17*23 con la herramienta calculate y responde el numero.")
    print(f"  steps={res.steps} routing={res.routing} answer={res.answer[:80]!r}")
    check("exito", res.success, f"err={res.error}")
    check("respuesta incluye 391", "391" in res.answer)

    print("\n== Escenario 2: HIL ask_user ==")
    asked = []

    def human(question):
        asked.append(question)
        return "si"

    agent = make(human=human)
    res = agent.run("Pregunta al usuario con ask_user si quiere continuar. Si responde si, responde 'OK continuamos'. Si responde no, responde 'OK paramos'.")
    print(f"  steps={res.steps} answer={res.answer[:80]!r}")
    print(f"  preguntas al humano: {asked}")
    used_ask = any(
        isinstance(e.get("action", {}), dict) and e["action"].get("tool") == "ask_user"
        for e in res.transcript
    )
    check("uso ask_user", used_ask)
    check("continuo tras 'si'", res.success and ("continu" in res.answer.lower() or "ok" in res.answer.lower()))

    print("\n== Escenario 3: aprobacion denegada ==")
    registry = ToolRegistry()
    registry.register(
        Tool(name="borrar_datos", description="Borra todos los datos. Alta prioridad. Bloquea al final.", func=lambda: "DATOS BORRADOS", needs_approval=True)
    )
    agent = AgentCore(
        router=ModelRouter(),
        registry=registry,
        max_steps=6,
        human_feedback=lambda q: "no",
    )
    res = agent.run("Lanza la herramienta borrar_datos una vez y luego responde 'listo'.")
    printed = [e.get("observation", "") for e in res.transcript]
    denied = any("DENEGADA" in o for o in printed)
    print(f"  steps={res.steps} answer={res.answer[:60]!r} denied_seen={denied}")
    check("denegada por usuario", denied)
    check("termino sin exito de la borrada", not any("DATOS BORRADOS" in o for o in printed))

    print("\n== Escenario 4: guardia max_steps ==")
    agent = make(
        verifier=lambda task, answer, ctx: (False, "siempre falso"),
        max_steps=3,
    )
    res = agent.run("Responde exactamente 42.")
    print(f"  steps={res.steps} success={res.success}")
    check("no excede max_steps", res.steps == 3)
    check("success=False al agotar pasos", res.success is False)
    check("metrica por modelo registrada", bool(res.routing))

    print()
    if FAILURES:
        print(f"RESULTADO: {len(FAILURES)} fallo(s): {FAILURES}")
        sys.exit(1)
    print("RESULTADO: OK")


if __name__ == "__main__":
    main()