import ast
from pathlib import Path

ARCHIVOS = [
    "agent_core.py",
    "reasoning_loop.py",
    "chat_brain.py",
    "human_intervention.py",
    "browser_agent_captcha.py",
    "browser_agent_core.py",
]

def resumir(path: Path):
    print(f"\n{'='*60}")
    print(f"ARCHIVO: {path.name}")
    print(f"{'='*60}")

    try:
        # utf-8-sig quita el BOM automaticamente si existe
        codigo = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(codigo)
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

    # Deteccion de codigo a nivel de modulo que se ejecuta al importar
    print(f"\n  EJECUCION AL IMPORTAR (fuera de funciones/clases):")
    top_level_calls = [
        n for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    if top_level_calls:
        for c in top_level_calls:
            try:
                print(f"    {ast.unparse(c)}")
            except Exception:
                print(f"    (llamada detectada en linea {c.lineno})")
    else:
        print("    (ninguna detectada - OK)")

for nombre in ARCHIVOS:
    p = Path(nombre)
    if p.exists():
        resumir(p)
    else:
        print(f"\nARCHIVO: {nombre} -> NO EXISTE")

print(f"\n\n{'#'*60}")
print("FIN DEL MAPA (parte 2)")
print(f"{'#'*60}")
