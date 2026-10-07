"""Guard: the strings the backend emits as span text carry no middle dot."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIDDLE_DOT = "·"

# Every module that writes span titles, details or components the trace panel shows.
# backend/routers/diagnostics.py stands in for the diagnostics emitter (there is no
# backend/db/diagnostics.py); journey_document.py builds the replayed journey record.
SCANNED = (
    "backend/agents/phase_04_production/concierge.py",
    "backend/routers/chat.py",
    "backend/mcp/*client*.py",
    "backend/routers/diagnostics.py",
    "backend/db/journey_document.py",
    "backend/agents/phase_05_workflow/*.py",
    "meridian_agentcore/app/MeridianConcierge/turn_trace.py",
    "meridian_agentcore/app/MeridianConcierge/main.py",
    # The Gateway Lambda summaries land in the trace "result" field.
    "meridian_agentcore/agentcore/gateway_targets/**/*.py",
)


def _scanned_files() -> list[Path]:
    return sorted({path for pattern in SCANNED for path in ROOT.glob(pattern)})


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Return ids of the Constant nodes that are module, class or function docstrings."""
    nodes = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            nodes.add(id(first.value))
    return nodes


def _dotted_literals(source: str) -> list[int]:
    """Return line numbers of non-docstring string literals holding a middle dot."""
    tree = ast.parse(source)
    skipped = _docstring_nodes(tree)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and MIDDLE_DOT in node.value
        and id(node) not in skipped
    ]


def test_span_text_emitters_have_no_middle_dot():
    files = _scanned_files()
    assert len(files) >= 14
    offences = [
        f"{path.relative_to(ROOT)}:{line}"
        for path in files
        for line in _dotted_literals(path.read_text(encoding="utf-8"))
    ]
    assert offences == []


def test_guard_sees_fstrings_dict_values_and_skips_docstrings():
    source = (
        '"""Module · docstring."""\n'
        "def f(x):\n"
        '    """Doc · string."""\n'
        '    a = f"a {x} · b"\n'
        '    b = {"component": "Aurora · rows"}\n'
        '    return a, b\n'
    )
    assert _dotted_literals(source) == [4, 5]
