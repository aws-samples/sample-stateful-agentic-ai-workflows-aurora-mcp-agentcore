"""The docs say what runs after Release 1."""

import re
from pathlib import Path

import pytest

from backend.config import BedrockConfig
from tests.test_docs_copy import public_markdown

MERIDIAN = Path(__file__).resolve().parents[1]
REPO = MERIDIAN.parent
INBOUND_JWT_DOC = (
    "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/inbound-jwt-authorizer.html"
)
GLOBAL_INFERENCE_DOC = (
    "https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html"
)
# "does not use the AgentCore Identity service." and the like; naming only its outbound side
# ("does not use the outbound side of AgentCore Identity") is what the README now says.
NO_AGENTCORE_IDENTITY = re.compile(r"not use (the )?AgentCore Identity( service)?[.,;)]")


def read(relative: str, base: Path = MERIDIAN) -> str:
    """The file's text with every run of whitespace as one space, so line wraps do not matter."""
    return " ".join((base / relative).read_text().split())


def test_the_readme_says_what_identity_means_here():
    readme = read("README.md")
    assert "Identity in Meridian means inbound JWT authorization" in readme
    assert "the backend, both Runtimes and the Gateway" in readme


def test_the_readme_says_agentcore_identity_is_used_inbound_and_not_outbound():
    readme = read("README.md")
    assert "inbound JWT authorizer" in readme and INBOUND_JWT_DOC in readme
    assert "does not use the outbound side of AgentCore Identity" in readme
    assert "the token vault, OAuth credential providers and on-behalf-of token exchange" in readme


def test_no_public_document_says_meridian_does_not_use_agentcore_identity():
    stale = [str(path.relative_to(REPO)) for path in public_markdown()
             if NO_AGENTCORE_IDENTITY.search(" ".join(path.read_text().split()))]
    assert stale == []


def test_the_readme_names_the_models_and_the_tools_each_phase_uses():
    readme = read("README.md")
    assert "Claude Sonnet 5.5 (`global.anthropic.claude-sonnet-5-5`" in readme
    assert "one parameterized `search_trips` statement" in readme
    assert "typed `search_trips`" in readme and "serves only the reference MCP agent" in readme


@pytest.mark.parametrize(("relative", "base"), [
    ("README.md", MERIDIAN), ("README.md", REPO), (".env.example", MERIDIAN),
    ("docs/AGENTCORE_DEPLOY_RUNBOOK.md", MERIDIAN),
    ("meridian_agentcore/app/MeridianConcierge/README.md", MERIDIAN),
    ("meridian_agentcore/agentcore/agentcore.template.json", MERIDIAN),
    ("meridian_agentcore/app/MeridianConcierge/model/load.py", MERIDIAN),
])
def test_no_default_still_names_the_retired_model(relative, base):
    assert "us.openai.gpt-6-sol" not in read(relative, base)
    assert "GPT-6 Sol" not in read(relative, base)


def test_the_backend_default_is_not_the_retired_model():
    assert BedrockConfig.DEFAULT_MODEL_ID == "global.anthropic.claude-sonnet-5-5"
    assert "us.openai.gpt-6-sol" not in read("backend/config.py")
    assert "GPT-6" not in read("backend/config.py")


def test_the_wrong_docstrings_are_gone():
    memory_agent = read("backend/agents/phase_04_production/memory_agent.py")
    memory_recall = read("backend/agents/phase_05_workflow/memory_recall.py")
    assert "registered on both this agent AND the concierge" not in memory_agent
    assert "Phase 4 = Strands-driven recall" not in memory_recall
    assert "calls the MemoryAgent recall methods directly" in memory_recall


def test_no_docstring_still_routes_phase_2_through_postgres_mcp():
    assert "mcp_search()` uses `backend/mcp/mcp_client.py`" not in read(
        "backend/agents/phase_02_mcp/agent.py")
    assert "attaches both this server and postgres-mcp-server" not in read(
        "backend/mcp/concierge_mcp_client.py")
    assert "Phase 2: Via MCP (awslabs.postgres-mcp-server)" not in read("backend/routers/chat.py")


def test_the_dead_recovery_helper_is_gone():
    assert "_append_recovery_memory_receipt" not in read("backend/routers/chat.py")


def test_structure_points_at_the_one_hybrid_module():
    structure = read("STRUCTURE.md")
    assert "carries the hybrid lexical and semantic candidate query a second" not in structure
    assert "`backend/retrieval/hybrid.py`" in structure
    assert "`backend/tools/`" in structure and "`backend/security/`" in structure


def test_the_agent_docs_do_not_route_phase_2_through_postgres_mcp():
    routing = read("backend/agents/README.md")
    assert "`mcp_search()`: MCP only (postgres-mcp-server)" not in routing
    assert "typed tools on the `meridian-concierge` MCP server" in routing
    assert "`@tool` semantic search (pgvector)" not in routing
    assert "Live routing to the PostgreSQL and" not in read("backend/agents/phase_02_mcp/README.md")
    assert "carries the hybrid" not in read("docs/CODE_WALKTHROUGH.md")
    assert "execute_keyword_search" not in read("docs/CODE_WALKTHROUGH.md")
    assert "through `mcp_session`" not in read("docs/CODE_WALKTHROUGH.md")


def test_operations_covers_migration_021_and_the_dry_run():
    operations = read("docs/OPERATIONS.md")
    assert "021_audit_outcome.sql" in operations and "--dry-run" in operations
    assert "`tool_calls` and `turn_error`" in operations and "tools_called" not in operations
    assert "smoke_bedrock_model.py" in operations


def test_the_readme_describes_the_audit_row_as_it_is_written():
    readme = read("README.md")
    assert "(identity, RLS scope and rows returned)" not in readme
    assert "completed turns link" not in readme
    for words in ("`tool_calls`", "`refused_by`", "`refusal_code`", "`access_audit_id`",
                  "`turn_error`", "Every Phase 4 turn that reaches the agent Runtime has one "
                  "row written for it, failed turns included", "If the row's own write fails, "
                  "there is no row", "write no row"):
        assert words in readme, words


def test_operations_says_the_backends_403_also_writes_a_deny_row():
    operations = read("docs/OPERATIONS.md")
    assert "The backend also writes a `traveler_access_audit` deny row" in operations


def test_the_iam_docs_cite_the_global_inference_policy_and_grant_each_role_its_models():
    operations = read("docs/OPERATIONS.md")
    runbook = read("docs/AGENTCORE_DEPLOY_RUNBOOK.md")
    assert GLOBAL_INFERENCE_DOC in operations and GLOBAL_INFERENCE_DOC in runbook
    assert "both Runtime execution roles" not in operations + runbook
    assert "`MeridianWorkflow` role" in operations and "`MeridianWorkflow` role" in runbook


def test_the_runtime_readme_states_the_model_as_it_is_built():
    concierge = read("meridian_agentcore/app/MeridianConcierge/README.md")
    assert "16,000-token output budget" in concierge
    assert "BEDROCK_REASONING_EFFORT" not in concierge
    assert "claude-haiku" not in concierge
    assert "no `temperature`, no forced tool choice and no disabled thinking" in concierge


def test_the_unused_outbound_identity_path_and_postgres_mcp_client_are_gone():
    assert not (MERIDIAN / "backend/mcp/mcp_client.py").exists()
    identity = read("backend/agentcore/identity.py")
    assert "get_resource_api_key" not in identity and "resource_provider" not in identity
    concierge = read("backend/agents/phase_04_production/concierge.py")
    assert "AgentCore Identity resolved" not in concierge


def test_structure_says_when_account_ids_are_masked():
    structure = read("STRUCTURE.md")
    assert "Read-time masking" not in structure
    assert "error text before it is stored or shown" in structure
