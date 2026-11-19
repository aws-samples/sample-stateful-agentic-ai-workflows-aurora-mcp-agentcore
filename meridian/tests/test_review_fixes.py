"""Review fixes: leftovers keep thread problems, and the guides describe the current behaviour."""

from pathlib import Path

from scripts.identity_probes.effects import AuroraCleanup
from tests.test_identity_effects import FakeRds

MERIDIAN = Path(__file__).resolve().parents[1]


def read(*parts: str) -> str:
    return (MERIDIAN.joinpath(*parts)).read_text(encoding="utf-8")


def test_a_thread_whose_holds_cannot_be_read_is_a_leftover_not_a_silent_pass():
    class Rds(FakeRds):
        def _rows(self, query, params):
            if "journey_threads" in query and "LIKE" in query:
                return [{"thread": "phase5-proof-idpabc12345wj"}]
            if "FROM journey_threads jt" in query:
                raise ConnectionError("down")
            return super()._rows(query, params)

    baseline = {"trv_meridian_demo": frozenset(), "trv_demo_decoy": frozenset()}
    items = AuroraCleanup(Rds(bookings={})).leftovers("phase5-proof-idpabc12345", baseline)

    assert any(i.startswith("thread phase5-proof-idpabc12345wj: reading the holds") for i in items)


def test_the_guides_say_the_warm_up_turns_write_and_the_validator_cleanup_needs_the_gateway():
    scripts = read("scripts", "README.md")
    assert "one read-only turn per phase" not in scripts
    assert "write conversation, memory and audit rows" in scripts and "place no hold" in scripts
    operations = read("docs", "OPERATIONS.md")
    assert "no governed release possible" in operations
    assert "no governed release possible" in read("scripts", "validate_demo.py")
    assert "Do not run the script bare" not in operations


def test_the_schema_hint_names_the_explicit_migration_commands():
    source = read("scripts", "init_aurora_schema.py")

    assert "Run --pending, then --dry-run --only" in source and "apply_migrations.py" in source


def test_no_changed_python_line_is_over_one_hundred_characters():
    for name in ("examples/memory_mcp_demo.py", "scripts/identity_probes/probes.py",
                 "scripts/identity_probes/runner.py", "scripts/lost_response_proof.py",
                 "tests/identity_proof_support.py", "tests/test_provision_preflight.py"):
        assert [n for n, line in enumerate(read(*name.split("/")).splitlines(), 1)
                if len(line) > 100 and "http" not in line] == [], name


def test_the_rls_example_has_no_em_dash_in_the_lines_this_branch_touched():
    assert "— i.e." not in read("examples", "rls_for_agents.sql")
