"""Nucleo del agente: orquestador ReAct sobre ModelRouter.

Pipeline del proyecto (mision):
    tarea -> decision (tool_call/answer) -> accion (tool) -> observacion
        -> verificar -> respuesta estructurada

Componentes:
- ToolRegistry: registro de herramientas con schema JSON y flag needs_approval.
- AgentCore: loop plan-accion-observacion, HIL (ask_user + aprobaciones),
    verificacion final y max_steps de seguridad.

Es aditivo: una vez/modelo/test a la vez. No toca e2e_runner.
"""

import ast
import json
import logging
import operator
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from model_router import ModelRouter, clean_json

logger = logging.getLogger(__name__)

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["tool_call", "answer"], "description": "tool_call para invocar una herramienta, answer para responder al usuario"},
        "tool": {"type": "string", "description": "nombre de la herramienta a invocar (solo con action=tool_call)"},
        "arguments": {"type": "object", "description": "argumentos de la herramienta (solo con action=tool_call)"},
        "answer": {"type": "string", "description": "respuesta final al usuario (solo con action=answer)"},
    },
    "required": ["action"],
}


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    parameters: Optional[dict] = None
    needs_approval: bool = False
    category: str = "core"

    def __call__(self, **kwargs: Any) -> Any:
        return self.func(**kwargs)


@dataclass
class AgentResult:
    task: str
    answer: str = ""
    success: bool = False
    steps: int = 0
    transcript: list = field(default_factory=list)
    routing: list = field(default_factory=list)
    error: Optional[str] = None
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "answer": self.answer,
            "success": self.success,
            "steps": self.steps,
            "routing": self.routing,
            "error": self.error,
            "duration_s": self.duration_s,
            "transcript": self.transcript,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"tool duplicada: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def decorator(self, name: str, description: str, parameters: Optional[dict] = None, needs_approval: bool = False, category: str = "core"):
        def wrapper(func: Callable[..., Any]) -> Tool:
            return self.register(Tool(name=name, description=description, func=func, parameters=parameters, needs_approval=needs_approval, category=category))
        return wrapper

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def call(self, name: str, arguments: dict) -> str:
        tool = self.get(name)
        if tool is None:
            return f"ERROR: herramienta desconocida '{name}'. Disponibles: {self.names()}"
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            result = tool(**arguments)
            if isinstance(result, (dict, list)):
                return json.dumps(result, ensure_ascii=False)
            return str(result)
        except Exception as exc:
            return f"ERROR en {name}: {type(exc).__name__}: {exc}"

    def describe(self) -> str:
        lines = []
        for tool in self._tools.values():
            params = json.dumps(tool.parameters or {"type": "object"}, ensure_ascii=False)
            approval = " (requiere aprobacion del usuario)" if tool.needs_approval else ""
            lines.append(f"- {tool.name}: {tool.description}{approval}\n  Params: {params}")
        return "\n".join(lines)


class AgentCore:
    def __init__(
        self,
        router: Optional[ModelRouter] = None,
        registry: Optional[ToolRegistry] = None,
        max_steps: int = 10,
        human_feedback: Optional[Callable[[str], str]] = None,
        verifier: Optional[Callable[[str, str, dict], tuple[bool, str]]] = None,
        system_prompt: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        self.router = router or ModelRouter()
        self.registry = registry or ToolRegistry()
        self.max_steps = max_steps
        self.human_feedback = human_feedback
        self.verifier = verifier
        self.verbose = verbose
        self._system_prompt = system_prompt
        self._register_builtin_tools()

    # ------------------------------------------------------------------
    # Herramientas incluidas
    # ------------------------------------------------------------------
    def _register_builtin_tools(self) -> None:
        calc_params = {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "expresion aritmetica, ej: (17*23)+2"}},
            "required": ["expression"],
        }

        def calculate(expression: str) -> str:
            return str(safe_eval(expression))

        self.registry.register(Tool(name="calculate", description="Evalua una expresion aritmetica y devuelve el numero.", func=calculate, parameters=calc_params))

        ask_params = {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "pregunta clara para el usuario, con opciones si aplican"}},
            "required": ["question"],
        }

        def ask_user(question: str) -> str:
            return self._ask_human(question)

        self.registry.register(Tool(name="ask_user", description="Detiene el trabajo y pregunta algo al usuario (datos faltantes, confirmaciones, 2FA, password, captcha). Devuelve la respuesta escrita del usuario.", func=ask_user, parameters=ask_params))

    def _ask_human(self, question: str) -> str:
        if self.human_feedback is not None:
            return self.human_feedback(question)
        return input(f"\n[AGENTE] {question}\nRespuesta: ")

    def _approve(self, tool: Tool, arguments: dict) -> bool:
        answer = self._ask_human(f"Se requiere aprobacion para: {tool.name} {json.dumps(arguments, ensure_ascii=False)}  (si/no)")
        return answer.strip().lower()[:3] in ("si", "sí", "yes", "s")

    # ------------------------------------------------------------------
    # Prompt / historial
    # ------------------------------------------------------------------
    def _build_system_prompt(self) -> str:
        if self._system_prompt:
            return self._system_prompt
        return (
            "Eres un agente que resuelve tareas reales usando herramientas y respondiendo al usuario.\n"
            "Reglas:\n"
            "- Responde SIEMPRE con UNA unica accion JSON. Nada mas, sin explicaciones previas.\n"
            "- Para usar una herramienta: {\"action\":\"tool_call\",\"tool\":\"<nombre>\",\"arguments\":{...}}\n"
            "- Para terminar: {\"action\":\"answer\",\"answer\":\"<respuesta final clara>\"}\n"
            "- Si falta informacion o hay ambiguedad, usa la herramienta ask_user ANTES de continuar.\n"
            "- Si una herramienta devuelve ERROR o falta informacion, consulta tu historial y prueba otra estrategia o responde.\n"
            "- No inventes numeros: obtenlos de la herramienta.\n"
            "Herramientas disponibles:\n"
            f"{self.registry.describe()}\n"
        )

    def _build_history(self, task: str, transcript: list[dict]) -> str:
        parts = [f"TAREA DEL USUARIO: {task}"]
        for entry in transcript[-8:]:
            action = entry.get("action", {})
            if isinstance(action, dict) and action.get("action") == "tool_call":
                parts.append(f"> ACCION: usar {action.get('tool')} con {json.dumps(action.get('arguments', {}), ensure_ascii=False)}")
            else:
                parts.append(f"> ACCION: {json.dumps(action, ensure_ascii=False)}")
            parts.append(f"  OBSERVACION: {entry.get('observation', '')[:1500]}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Loop principal
    # ------------------------------------------------------------------
    def run(self, task: str, max_tokens: int = 1024) -> AgentResult:
        start = time.time()
        result = AgentResult(task=task)
        transcript: list[dict] = []
        repeated: dict[str, int] = {}
        system = self._build_system_prompt()

        for step in range(1, self.max_steps + 1):
            history = self._build_history(task, transcript)
            res = self.router.generate_text(
                prompt=history,
                system_prompt=system,
                schema=ACTION_SCHEMA,
                max_tokens=max_tokens,
                temperature=0.0,
            )
            result.routing.append(f"step{step}={res.provider}/{res.model}")
            if self.verbose:
                print(f"[step {step}] {res.provider}/{res.model} -> {res.text[:200]!r}")

            if not res.text:
                observation = f"ERROR: el modelo no devolvio accion ({res.error or 'vacio'}). Reintenta o responde."
                transcript.append({"action": {}, "observation": observation})
                continue

            parsed = self._parse_action(res.text)
            if parsed is None:
                observation = f"ERROR: accion no parseable: {res.text[:200]}. Responde SOLO con JSON valido."
                transcript.append({"action": {}, "observation": observation})
                continue

            action = parsed.get("action")
            if action == "answer":
                answer = str(parsed.get("answer", "")).strip()
                result.answer = answer
                result.steps = step
                if self.verifier is None:
                    result.success = True
                    return self._finish(result, transcript, start)
                ok, reason = self.verifier(task, answer, self._context(transcript))
                if ok:
                    result.success = True
                    return self._finish(result, transcript, start)
                observation = f"VERIFICACION: respuesta incorrecta. Motivo: {reason}. Corrige tu respuesta."
                transcript.append({"action": parsed, "observation": observation})
                if step >= self.max_steps:
                    break
                continue

            if action == "tool_call":
                tool_name = str(parsed.get("tool", "")).strip()
                arguments = parsed.get("arguments") or {}
                tool = self.registry.get(tool_name)
                if tool is None:
                    observation = f"ERROR: herramienta desconocida '{tool_name}'. Disponibles: {self.registry.names()}"
                    transcript.append({"action": parsed, "observation": observation})
                    continue

                sig = f"{tool_name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
                repeated[sig] = repeated.get(sig, 0) + 1
                if repeated[sig] > 2:
                    observation = f"ERROR: repites la misma accion ({sig}) sin progreso. Cambia de estrategia o da tu answer."
                    transcript.append({"action": parsed, "observation": observation})
                    continue

                if tool.needs_approval and not self._approve(tool, arguments):
                    observation = f"Accion {tool_name} DENEGADA por el usuario."
                    transcript.append({"action": parsed, "observation": observation})
                    continue

                observation = self.registry.call(tool_name, arguments)
                transcript.append({"action": parsed, "observation": observation})
                continue

            observation = f"ERROR: action desconocida '{action}'. Usa tool_call o answer."
            transcript.append({"action": parsed, "observation": observation})

        result.steps = self.max_steps
        if not result.answer:
            result.answer = "No pude completar la tarea en el numero maximo de pasos."
        result.success = False
        return self._finish(result, transcript, start)

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def _parse_action(self, text: str) -> Optional[dict]:
        cleaned = clean_json(text)
        if cleaned is None:
            return None
        try:
            parsed = json.loads(cleaned)
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    def _context(self, transcript: list[dict]) -> dict:
        return {"steps": len(transcript), "observations": [e.get("observation", "") for e in transcript[-5:]]}

    def _finish(self, result: AgentResult, transcript: list[dict], start: float) -> AgentResult:
        result.transcript = transcript
        result.duration_s = round(time.time() - start, 3)
        return result


def safe_eval(expression: str) -> float:
    """Evalua expresiones aritmeticas seguras (sin exec/eval de Python)."""
    tree = ast.parse(expression, mode="eval")

    def _node(node: ast.AST):
        if isinstance(node, ast.Expression):
            return _node(node.body)
        if isinstance(node, ast.BinOp):
            ops = {
                ast.Add: operator.add,
                ast.Sub: operator.sub,
                ast.Mult: operator.mul,
                ast.Div: operator.truediv,
                ast.FloorDiv: operator.floordiv,
                ast.Mod: operator.mod,
                ast.Pow: operator.pow,
            }
            op_type = type(node.op)
            if op_type not in ops:
                raise ValueError(f"operador no permitido: {op_type.__name__}")
            return ops[op_type](_node(node.left), _node(node.right))
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                return -_node(node.operand)
            if isinstance(node.op, ast.UAdd):
                return _node(node.operand)
            raise ValueError("operador unario no permitido")
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"expresion no permitida: {type(node).__name__}")

    return _node(tree)