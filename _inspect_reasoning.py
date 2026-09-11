import ast
from pathlib import Path

codigo = Path("reasoning_loop.py").read_text(encoding="utf-8-sig")
tree = ast.parse(codigo)

print("="*60)
print("CAMPOS DE PlanStep Y FinalPlan (modelos pydantic)")
print("="*60)

for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name in ("PlanStep", "FinalPlan"):
        print(f"\nclass {node.name}:")
        for item in node.body:
            if isinstance(item, ast.AnnAssign):
                nombre = item.target.id if isinstance(item.target, ast.Name) else "?"
                try:
                    tipo = ast.unparse(item.annotation)
                except Exception:
                    tipo = "?"
                default = ""
                if item.value is not None:
                    try:
                        default = f" = {ast.unparse(item.value)}"
                    except Exception:
                        default = " = ?"
                print(f"    {nombre}: {tipo}{default}")

print()
print("="*60)
print("CUERPO COMPLETO DE run_reasoning()")
print("="*60)

for node in ast.walk(tree):
    if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_reasoning":
        print(ast.unparse(node))

print()
print("="*60)
print("CUERPO COMPLETO DE main()")
print("="*60)

for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == "main":
        print(ast.unparse(node))
