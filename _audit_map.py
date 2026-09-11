import ast
from pathlib import Path

ARCHIVOS = [
    "agent_core.py",
    "core_agent.py",
    "reasoning_loop.py",
    "chat_brain.py",
    "model_router.py",
    "human_intervention.py",
    "browser_agent_captcha.py",
    "browser_agent_core.py",
]

def resumir(path: Path):
    print(f"\n{'='*60}")
    print(f"ARCHIVO: {path.name}")
    print(f"{'='*60}")

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ERROR al parsear: {e}")
        return

    imports = []
    clases = []
    funciones = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modulo = node.module or ""
            nombres = ", ".join(n.name for n in node.names)
            imports.append(f"from {modulo} import {nombres}")
        elif isinstance(node, ast.Import):
            for n in node.names:
                imports.append(f"import {n.name}")

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            metodos = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            clases.append((node.name, metodos))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            tipo = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            funciones.append(f"{tipo} {node.name}({', '.join(args)})")

    print(f"\n  IMPORTS LOCALES/RELEVANTES:")
    locales = [i for i in imports if not any(i.startswith(f"import {m}") or i.startswith(f"from {m}") for m in ["asyncio","json","os","sys","pathlib","typing","dataclasses","logging","re","time","datetime"])]
    for i in locales[:15]:
        print(f"    {i}")

    print(f"\n  CLASES:")
    if clases:
        for nombre, metodos in clases:
            print(f"    class {nombre}:")
            for m in metodos:
                print(f"        - {m}()")
    else:
        print("    (ninguna)")

    print(f"\n  FUNCIONES TOP-LEVEL:")
    if funciones:
        for f in funciones:
            print(f"    {f}")
    else:
        print("    (ninguna)")

for nombre in ARCHIVOS:
    p = Path(nombre)
    if p.exists():
        resumir(p)
    else:
        print(f"\n{'='*60}")
        print(f"ARCHIVO: {nombre} -> NO EXISTE")
        print(f"{'='*60}")

print(f"\n\n{'#'*60}")
print("FIN DEL MAPA")
print(f"{'#'*60}")
