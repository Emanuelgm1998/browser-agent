"""
execute_plan.py

Conecta el Planner+Reviewer (reasoning_loop.py) con el Executor (core_agent.AgentCore).
"""

import asyncio
import json
import sys

from reasoning_loop import run_reasoning, FinalPlan
from core_agent import AgentCore, ToolRegistry, Tool
from browser_agent_core import search_google
from browser_agent_captcha import search_google_persistent
from human_intervention import wait_for_human


def plan_to_task_string(plan: FinalPlan) -> str:
    lineas = [f"OBJETIVO: {plan.goal}", "", f"RESUMEN: {plan.summary}", "", "PASOS A SEGUIR:"]
    for i, step in enumerate(plan.steps, start=1):
        destino = f" (target: {step.target})" if step.target else ""
        lineas.append(f"  {i}. [{step.action}] {step.description}{destino}")
    if plan.risks:
        lineas.append("")
        lineas.append("RIESGOS A CONSIDERAR:")
        for r in plan.risks:
            lineas.append(f"  - {r}")
    return "\n".join(lineas)


def human_feedback_bridge(question: str) -> str:
    """
    Adaptador CORREGIDO.

    wait_for_human(reason, instruction) -> bool es un GATE de pausa/reanudacion
    (CONTINUAR/CANCELAR), NO un mecanismo de pregunta abierta. Devuelve bool,
    no texto libre del usuario.

    AgentCore.human_feedback necesita Callable[[str], str] porque el resultado
    se inyecta como observacion textual en el transcript del modelo.

    Este bridge:
    1. Usa el gate binario para pausar y avisar al humano.
    2. Convierte el bool resultante en un string interpretable por el modelo.
    3. Si el humano cancela, propaga una senal clara de cancelacion, no un
       texto ambiguo tipo "False".
    """
    aprobado = wait_for_human(
        reason="agent_core_ask_user",
        instruction=(
            f"El agente necesita tu ayuda con lo siguiente:\n\n{question}\n\n"
            "Resuelve lo que haga falta manualmente en el navegador o en tu "
            "entorno, y escribe CONTINUAR para que el agente siga (asumiendo "
            "que ya se resolvio), o CANCELAR para detener la tarea."
        ),
    )

    if aprobado:
        return (
            "El usuario confirmo que la situacion fue resuelta manualmente "
            "(CONTINUAR). Procede asumiendo que el obstaculo ya no existe."
        )
    return (
        "El usuario CANCELO la tarea. No debes intentar continuar; "
        "responde con action=answer explicando que la tarea fue cancelada "
        "por el usuario."
    )


def build_registry_with_browser_tools() -> ToolRegistry:
    registry = ToolRegistry()

    search_params = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "termino de busqueda"},
        },
        "required": ["query"],
    }

    def google_search_tool(query: str) -> str:
        resultados = asyncio.run(search_google(query=query, headless=False, max_results=20))
        return json.dumps(resultados, ensure_ascii=False)

    registry.register(Tool(
        name="google_search",
        description="Busca en Google (sesion nueva cada vez) y devuelve una lista de {texto, url}.",
        func=google_search_tool,
        parameters=search_params,
    ))

    def google_search_persistent_tool(query: str) -> str:
        resultados = asyncio.run(search_google_persistent(query=query, headless=False))
        return json.dumps(resultados, ensure_ascii=False)

    registry.register(Tool(
        name="google_search_persistent",
        description="Busca en Google con sesion persistente y deteccion/pausa de CAPTCHA. Usar si google_search falla repetidamente o Google bloquea.",
        func=google_search_persistent_tool,
        parameters=search_params,
    ))

    return registry


def main() -> None:
    if len(sys.argv) < 2:
        print("")
        print("Uso:")
        print("")
        print('python execute_plan.py "tu tarea"')
        print("")
        sys.exit(1)

    task = " ".join(sys.argv[1:]).strip()

    plan = asyncio.run(run_reasoning(task))

    if plan is None:
        print("")
        print("=" * 70)
        print(" NADA QUE EJECUTAR")
        print("=" * 70)
        print("")
        print("El plan no fue validado o no fue aprobado por el usuario.")
        return

    print("")
    print("=" * 70)
    print(" FASE 2: EJECUCION CON AgentCore")
    print("=" * 70)
    print("")

    task_string = plan_to_task_string(plan)
    print(task_string)
    print("")

    registry = build_registry_with_browser_tools()
    agent = AgentCore(
        registry=registry,
        human_feedback=human_feedback_bridge,
        max_steps=15,
        verbose=True,
    )

    result = agent.run(task_string)

    print("")
    print("=" * 70)
    print(" RESULTADO DE LA EJECUCION")
    print("=" * 70)
    print("")
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
