"""The docs say what runs after Release 3: governed search and release, and the audit row."""

from pathlib import Path

from tests.test_docs_release_1 import read

MERIDIAN = Path(__file__).resolve().parents[1]
RELEASE_TOOL = "MeridianHolds___release_hold"


def test_the_architecture_docs_name_the_gateway_release_and_the_search():
    for relative in ("AGENTS.md", "docs/STATEFUL_ARCHITECTURE.md", "docs/OPERATIONS.md"):
        text = read(relative)
        assert RELEASE_TOOL in text, relative
        assert "MeridianCatalog___hybrid_trip_search" in text, relative


def test_the_docs_describe_the_workflow_audit_row():
    for relative in ("AGENTS.md", "README.md", "docs/STATEFUL_ARCHITECTURE.md"):
        text = read(relative)
        assert "workflow_run" in text, relative


def test_the_gateway_has_eleven_tools_and_the_model_is_offered_ten():
    runbook = read("docs/AGENTCORE_DEPLOY_RUNBOOK.md")
    assert "ten tools" not in runbook
    assert "eleven tools" in runbook
    assert "expect eleven tools" in read("docs/OPERATIONS.md")
    assert "ten of the Gateway's eleven tools" in read("README.md")


def test_the_cedar_policy_counts_include_the_release_rule():
    runbook = read("docs/AGENTCORE_DEPLOY_RUNBOOK.md")
    assert "meridian_release_governance" in runbook
    assert "its four Cedar policies (five in `jwt` mode" in runbook
    assert "meridian_release_governance" in read("docs/OPERATIONS.md")


def test_the_readmes_do_not_name_the_removed_pieces():
    for relative in ("README.md", "../README.md"):
        text = read(relative).lower()
        for word in ("supervisor", "semantictripsearchlambda"):
            assert word not in text, (relative, word)


def test_the_quick_start_expects_the_runtime_unconfigured_before_a_deploy():
    readme = read("../README.md")
    assert "`\"workflow_runtime_configured\"` is `false` until you deploy" in readme
    assert "`\"checkpoint_backend\": \"Aurora workflow_snapshots\"`" in readme
    assert "seed_data.py` calls Amazon Bedrock" in readme


def test_the_phase_guides_say_live_check_not_checkpoint():
    for folder in ("phase_01_sql", "phase_02_mcp", "phase_03_retrieval",
                   "phase_04_production", "phase_05_workflow"):
        guide = (MERIDIAN / "backend" / "agents" / folder / "README.md").read_text()
        assert "## Live check\n" in guide, folder
        assert "Live checkpoint" not in guide, folder


def test_the_other_graph_framework_is_named_only_by_its_example_path():
    for relative in ("README.md", "../README.md", "STRUCTURE.md", "docs/CODE_WALKTHROUGH.md",
                     "docs/STATEFUL_ARCHITECTURE.md"):
        text = read(relative).lower()
        for allowed in ("examples/langgraph", "test_app_never_imports_langgraph"):
            text = text.replace(allowed, "")
        assert "langgraph" not in text, relative


def test_the_quick_start_scripts_do_not_call_the_database_a_demo():
    seed = (MERIDIAN / "scripts" / "seed_data.py").read_text()
    init = (MERIDIAN / "scripts" / "init_aurora_schema.py").read_text()
    assert "requires an empty database." in seed
    assert "Seed a new Meridian database." in seed
    assert "empty demo" not in seed and "Meridian demo" not in seed
    assert "upgrade an existing database." in init
