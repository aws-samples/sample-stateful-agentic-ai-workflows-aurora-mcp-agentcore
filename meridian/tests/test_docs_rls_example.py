"""The RLS teaching example and the environment template describe the current identity model."""

import re
from pathlib import Path

MERIDIAN = Path(__file__).resolve().parents[1]
SQL = (MERIDIAN / "examples" / "rls_for_agents.sql").read_text(encoding="utf-8")
ENV = (MERIDIAN / ".env.example").read_text(encoding="utf-8")
AGENT = (MERIDIAN / "backend" / "agents" / "phase_04_production" / "production_agent.py"
         ).read_text(encoding="utf-8")
STALE = (
    "Each turn runs in ONE short DB transaction",
    "OUR secret maps to the cluster\n--   MASTER user",
    "We keep the master\n--   secret and step down",
    "A multi-user hosted application should\n--   additionally authenticate the end user",
)


def test_the_example_names_the_three_transactions_where_the_code_opens_them():
    for unit in ("_authorized_read", "_audit_unit", "_memory_unit"):
        assert re.search(rf"async def {unit}\(", AGENT), f"{unit} moved: update the example"
        assert unit in SQL
    assert "optional" in SQL.split("_memory_unit")[1][:120]
    assert "NO transaction\n--          open" in SQL


def test_the_example_names_the_hosted_backend_login_and_the_cognito_binding():
    assert "connects as the least-privilege login meridian_backend" in SQL
    assert "select AURORA_BACKEND_SECRET_ARN" in SQL
    assert "traveler_id claim" in SQL and "'cognito' row" in SQL
    assert "verified Cognito subject" in SQL


def test_the_example_no_longer_describes_the_older_identity_model():
    assert [text for text in STALE if text in SQL] == []


def test_the_environment_template_names_the_data_api_snapshot_path_and_the_direct_transport():
    assert "written through the RDS Data API" in ENV
    assert "the only snapshot path in this repository" in ENV
    assert "optional direct PostgreSQL\n# transport" in ENV
    assert "direct psycopg path (Phase 5 snapshots)" not in ENV


def test_nothing_in_the_application_opens_the_direct_connection_the_template_calls_optional():
    opened = [path for root in ("backend", "scripts", "meridian_agentcore/app")
              for path in (MERIDIAN / root).rglob("*.py")
              if re.search(r"^\s*(import|from) (psycopg|asyncpg|pg8000)", path.read_text(
                  encoding="utf-8"), re.MULTILINE)]

    assert opened == []
