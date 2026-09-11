import sys
from execute_plan import plan_to_task_string
from core_agent import AgentCore
from reasoning_loop import FinalPlan

print("="*60)
print(" SMOKE TEST DIRECTO DE EXECUTOR (AgentCore Bypass)")
print("="*60)

plan_sintetico = FinalPlan(
    goal="Calcula 47*12 y formatea el resultado",
    summary="Cálculo directo identificado por el loop de razonamiento.",
    steps=[
        {
            "id": 1,
            "description": "Usa la herramienta calculate para multiplicar 47 por 12",
            "action": "calculate",
            "tool": "calculate"
        },
        {
            "id": 2,
            "description": "Formatea y entrega la respuesta final",
            "action": "format_output",
            "tool": "ask_user"
        }
    ],
    risks=["Ninguno"],
    requires_human_approval=False
)

task_str = plan_to_task_string(plan_sintetico)
print("\n[PLAN INYECTADO]:")
print(task_str)

print("\n[INICIANDO AGENTCORE]...")
agent = AgentCore()
result = agent.run(task_str)

print("\n" + "="*60)
print(" RESULTADO DE AGENTCORE")
print("="*60)
print(f"Éxito: {result.success}")
print(f"Respuesta Final: {result.answer}")
print(f"Pasos tomados: {result.steps}")
print(f"Duración: {result.duration_s:.2f}s")
