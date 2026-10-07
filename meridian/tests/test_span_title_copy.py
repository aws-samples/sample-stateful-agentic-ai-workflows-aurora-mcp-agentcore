"""Guard: span titles the backend writes carry no middle dot."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("backend", "meridian_agentcore/app")
MIDDLE_DOT = "·"


def _string_parts(node: ast.expr) -> list[str]:
    """Return every string literal piece inside a title expression."""
    return [
        part.value
        for part in ast.walk(node)
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    ]


def _activity_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", getattr(node.func, "attr", ""))
        if name == "create_activity":
            yield node


def _offences() -> list[str]:
    found = []
    for directory in SCAN_DIRS:
        for path in sorted((ROOT / directory).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for call in _activity_calls(tree):
                for keyword in call.keywords:
                    if keyword.arg != "title":
                        continue
                    if any(MIDDLE_DOT in text for text in _string_parts(keyword.value)):
                        found.append(f"{path.relative_to(ROOT)}:{call.lineno}")
    return found


def test_create_activity_titles_have_no_middle_dot():
    assert _offences() == []


def test_guard_sees_create_activity_calls():
    sample = ast.parse('create_activity(title=f"a {x} \\u00b7 b")')
    calls = list(_activity_calls(sample))
    assert len(calls) == 1
    assert any(MIDDLE_DOT in text for text in _string_parts(calls[0].keywords[0].value))
