"""Extract named functions/classes from the code base into paper/listings/ so the paper's code excerpts are always real source."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "listings"
OUT.mkdir(exist_ok=True)

# (output name, file, qualified name)
TARGETS = [
    ("repair_json.py", "backend/app/llm/toolparse.py", "repair_json"),
    ("cancellable.py", "backend/app/llm/gateway.py", "_cancellable"),
    ("classify_command.py", "backend/app/security.py", "classify_command"),
    ("resolve_in_workspace.py", "backend/app/security.py", "resolve_in_workspace"),
    ("decide.py", "backend/app/agent/permissions.py", "decide"),
    ("repair_tool_pairs.py", "backend/app/agent/context.py", "repair_tool_pairs"),
    ("compact_history.py", "backend/app/agent/context.py", "compact_history"),
    ("embed.py", "backend/app/agent/memory.py", "embed"),
    ("atomic_write.py", "backend/app/tools/fs_tools.py", "atomic_write"),
    ("recover_interrupted.py", "backend/app/agent/runtime.py", "AgentRuntime.recover_interrupted"),
    ("edit_file.py", "backend/app/tools/fs_tools.py", "EditFile"),
    ("tool_base.py", "backend/app/tools/base.py", "Tool"),
]


def find(tree: ast.AST, qual: str):
    parts = qual.split(".")
    nodes = list(tree.body)  # type: ignore[attr-defined]
    node = None
    for p in parts:
        node = next((n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == p), None)
        if node is None:
            raise KeyError(qual)
        nodes = getattr(node, "body", [])
    return node


for out, file, qual in TARGETS:
    src = (ROOT / file).read_text(encoding="utf-8")
    node = find(ast.parse(src), qual)
    start = (node.decorator_list[0].lineno if node.decorator_list else node.lineno) - 1
    lines = src.splitlines()[start:node.end_lineno]
    # de-indent methods
    indent = len(lines[0]) - len(lines[0].lstrip())
    text = "\n".join(l[indent:] for l in lines)
    text = "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in text)  # keep listings pure ASCII for LaTeX
    (OUT / out).write_text(f"# {file} :: {qual}\n{text}\n", encoding="utf-8")
    print(f"{out:28s} {len(lines):3d} lines  <- {file}::{qual}")
