"""The guides describe the briefing view and the graph example for what they do."""

from pathlib import Path

MERIDIAN = Path(__file__).resolve().parents[1]
ROOT = MERIDIAN.parent
HOOK = (MERIDIAN / "frontend" / "src" / "showcase" / "hooks" / "useMeridianShowcase.ts"
        ).read_text(encoding="utf-8")
GRAPH = (MERIDIAN / "examples" / "langgraph" / "pause_resume_graph.py").read_text(encoding="utf-8")
DOCS = {
    path: path.read_text(encoding="utf-8")
    for path in (
        ROOT / "README.md", MERIDIAN / "README.md", MERIDIAN / "STRUCTURE.md",
        MERIDIAN / "AGENTS.md", MERIDIAN / "examples" / "langgraph" / "README.md",
        MERIDIAN / "docs" / "STATEFUL_ARCHITECTURE.md",
    )
}


def briefing_row() -> str:
    readme = DOCS[MERIDIAN / "README.md"]
    return next(line for line in readme.splitlines() if line.startswith("| Solution briefing"))


def test_the_briefing_row_does_not_claim_the_view_makes_no_service_calls():
    row = briefing_row()

    assert "makes no service calls" not in row
    assert "health endpoint" in row and "catalog" in row and "profile" in row
    assert "every 30 seconds" in row


def test_the_polling_the_row_describes_is_what_the_shared_hook_does():
    assert "fetchHealth" in HOOK and "fetchProducts" in HOOK and "fetchMemoryProfile" in HOOK
    assert "}, 30000);" in HOOK and "if (!document.hidden" in HOOK


def test_no_guide_calls_the_graph_example_a_second_implementation_of_the_workflow():
    stale = [str(path.relative_to(ROOT)) for path, text in DOCS.items()
             if "second implementation" in text.lower() or "second workflow" in text.lower()]

    assert stale == []


def test_the_example_readmes_label_it_a_persistence_and_pause_resume_example():
    for path in (ROOT / "README.md", MERIDIAN / "README.md", MERIDIAN / "STRUCTURE.md",
                 MERIDIAN / "examples" / "langgraph" / "README.md"):
        assert "persistence and pause/resume example" in DOCS[path].lower(), path
    assert "not the governed workflow" in DOCS[ROOT / "README.md"]


def test_the_label_matches_the_example_graph_it_synthesizes_options_and_holds_nothing_real():
    assert "option A" in GRAPH and "held" in GRAPH
    assert "persistence and pause/resume example" in GRAPH
    assert "not Meridian's governed workflow" in GRAPH
    code = GRAPH.split('"""', 2)[2]
    assert "Gateway" not in code and "release_hold" not in code
