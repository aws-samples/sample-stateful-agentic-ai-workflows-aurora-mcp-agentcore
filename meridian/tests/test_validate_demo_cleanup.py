"""The demo validator gives back its holds and keeps every row, unless told to delete."""

import pytest

from scripts import validate_demo
from tests.governed_release_support import JORDAN, FakeAurora, install_gateway

CONVERSATION = "owned-conversation"
EXPORTED_AUDIT = {"booking_id": "bk-hold", "operation": "hold", "exported": True}


@pytest.fixture
def run(monkeypatch):
    """The validator's steps: a direct hold that was confirmed, and a workflow run with a hold."""
    db = FakeAurora(
        {"bk-hold": ("held", "jrn-workflow"), "bk-confirmed": ("confirmed", "jrn-direct"),
         "bk-other": ("held", "jrn-other")},
        threads={CONVERSATION: "jrn-workflow", "other-conversation": "jrn-other"},
        audit=[EXPORTED_AUDIT])
    monkeypatch.setattr(validate_demo, "TRAVELER", JORDAN)
    monkeypatch.setattr(validate_demo, "steps", [
        {"step": "p5_resume", "conversation_id": CONVERSATION},
        {"step": "p4_order_hold", "order": {"order_id": "bk-confirmed", "status": "held"}},
        {"step": "p4_confirm_booking", "order": {"order_id": "bk-confirmed",
                                                  "status": "confirmed"}},
    ])
    monkeypatch.setattr("backend.db.rds_data_client.get_rds_data_client", lambda: db)
    return db, install_gateway(monkeypatch, db)


async def test_the_default_cleanup_releases_its_hold_and_deletes_no_row(run):
    db, gateway = run

    assert await validate_demo.cleanup() is True

    assert gateway.calls == ["bk-hold"]
    assert db.status["bk-hold"] == "released"
    assert db.deletes() == [] and db.audit == [EXPORTED_AUDIT]


async def test_an_unrelated_booking_is_never_touched(run):
    db, gateway = run

    await validate_demo.cleanup()

    assert db.status["bk-other"] == "held" and "bk-other" not in gateway.calls
    touched = {value for _sql, params in db.statements for value in params}
    assert not touched & {"bk-other", "jrn-other", "other-conversation"}


async def test_the_confirmed_booking_is_kept_and_reported_by_id(run, capsys):
    db, gateway = run

    await validate_demo.cleanup()

    assert db.status["bk-confirmed"] == "confirmed" and "bk-confirmed" not in gateway.calls
    assert "kept confirmed booking bk-confirmed" in capsys.readouterr().out


async def test_a_hold_that_stays_held_makes_the_cleanup_unclean(run, monkeypatch, capsys):
    db, gateway = run
    monkeypatch.setattr(gateway, "call_tool", lambda name, args: {"result": {"content": [
        {"type": "text", "text": '{"error": "hold_not_found"}'}]}})

    assert await validate_demo.cleanup() is False

    assert "Cleanup FAILED" in capsys.readouterr().err and db.deletes() == []


async def test_the_delete_flag_is_the_only_path_that_deletes(run, monkeypatch):
    db, _gateway = run

    async def purge(client, journey, thread):
        client.statements.append((f"DELETE journey {journey} thread {thread}", ()))

    monkeypatch.setattr("scripts.kill_and_resume_proof._purge", purge)
    await validate_demo.cleanup(delete=True)

    deleted = " ".join(db.deletes())
    assert "DELETE FROM booking_action_audit" in deleted and "DELETE journey" in deleted


def test_the_command_line_names_the_disposable_database_flag():
    parser = validate_demo.build_parser()

    assert parser.parse_args([]).delete is False
    assert parser.parse_args(["--delete-from-disposable-database"]).delete is True
    assert "audit rows included" in parser.format_help()


@pytest.mark.parametrize(("message", "reports"), [
    ("Tuscany Wine & Wellness has 6 nights with 5 places left and 8 nights with 3 places left.",
     True),
    ("The 6 nights option has 5 package places left.", True),
    ("Only 1 night remains, with 1 place left.", True),
    ("The 6-night option has 5 places left, and the 8-night option has 3 places left.", True),
    ("Six nights remain, with five places left.", True),
    ("Tuscany Wine & Wellness has two lengths available.", False),
    ("It has 5 places left.", False),
    ("A 7-night stay in Tuscany, with 3 places to visit.", False),
    ("The 6-night option has 5 places remaining.", True),
])
def test_the_inventory_check_reads_the_facts_not_one_phrase(message, reports):
    assert validate_demo.reports_inventory(message) is reports


async def test_a_confirmed_booking_on_a_run_thread_is_handled_once_and_never_called_released(
        monkeypatch, capsys):
    db = FakeAurora(
        {"bk-hold": ("held", "jrn-workflow"), "bk-confirmed": ("confirmed", "jrn-concierge")},
        threads={CONVERSATION: "jrn-workflow", f"concierge:{CONVERSATION}": "jrn-concierge"})
    monkeypatch.setattr(validate_demo, "TRAVELER", JORDAN)
    monkeypatch.setattr(validate_demo, "steps", [
        {"step": "p5_resume", "conversation_id": CONVERSATION},
        {"step": "p4_confirm_booking", "order": {"order_id": "bk-confirmed",
                                                  "status": "confirmed"}},
    ])
    monkeypatch.setattr("backend.db.rds_data_client.get_rds_data_client", lambda: db)
    gateway = install_gateway(monkeypatch, db)

    assert await validate_demo.cleanup() is True

    out = capsys.readouterr().out
    assert gateway.calls == ["bk-hold"]
    assert out.count("released: booking bk-hold") == 1
    assert "released: booking bk-confirmed" not in out
    assert out.count("already inactive, no release call: booking bk-confirmed") == 1
    assert out.count("kept confirmed booking bk-confirmed") == 1


# The shape GET /api/products returns: {"products": [{"product_id", "price", "availability"}]}.
CATALOG = [
    {"product_id": "CTY-002", "price": 2499.0, "availability": {"5 nights": 10, "7 nights": 5}},
    {"product_id": "LUX-001", "price": 9999.0, "availability": {"7 nights": 40}},
    {"product_id": "BIZ-002", "price": 1299.0, "availability": {"2 nights": 28, "3 nights": 20}},
    {"product_id": "TIE-001", "price": 999.0, "availability": {"2 nights": 28}},
    {"product_id": "OUT-001", "price": 500.0, "availability": {"3 nights": 0}},
]


def test_the_confirmation_goes_to_the_roomiest_choice_the_budget_covers():
    assert validate_demo.roomiest_target(CATALOG, 320000) == ("BIZ-002", "2 nights")


def test_a_package_over_budget_is_never_chosen_however_roomy():
    assert validate_demo.roomiest_target(CATALOG, 320000) != ("LUX-001", "7 nights")
    assert validate_demo.roomiest_target(CATALOG, None) == ("LUX-001", "7 nights")


def test_nothing_affordable_with_a_place_left_means_no_target():
    assert validate_demo.roomiest_target(CATALOG, 100) is None
    assert validate_demo.roomiest_target([CATALOG[-1]], None) is None


def test_an_unreadable_catalog_falls_back_to_the_fixed_choice(monkeypatch):
    class Broken:
        def get(self, *_args, **_kwargs):
            raise validate_demo.httpx.ConnectError("catalog down")

    monkeypatch.setattr(validate_demo, "client", Broken(), raising=False)

    assert validate_demo.confirm_target({"budget_ceiling_per_traveler_cents": 320000}) == (
        "CTY-002", "7 nights")


def test_the_catalog_list_is_read_in_the_shape_the_api_returns(monkeypatch):
    class Catalog:
        def get(self, path, params=None):
            assert path == "/api/products" and params == {"limit": 100}
            return validate_demo.httpx.Response(
                200, json={"products": CATALOG, "total": len(CATALOG)},
                request=validate_demo.httpx.Request("GET", "http://test/api/products"))

    monkeypatch.setattr(validate_demo, "client", Catalog(), raising=False)

    assert validate_demo.confirm_target({"budget_ceiling_per_traveler_cents": 320000}) == (
        "BIZ-002", "2 nights")


def test_an_unknown_budget_uses_the_fixed_choice_without_reading_the_catalog(monkeypatch):
    class NoCatalog:
        def get(self, *_args, **_kwargs):
            raise AssertionError("the catalog is not read without a budget")

    monkeypatch.setattr(validate_demo, "client", NoCatalog(), raising=False)

    assert validate_demo.confirm_target({}) == ("CTY-002", "7 nights")
    assert validate_demo.confirm_target(None) == ("CTY-002", "7 nights")
