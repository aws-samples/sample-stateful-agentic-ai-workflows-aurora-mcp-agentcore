"""LOG_LEVEL and LOG_JSON apply to every module's logger, and traveler text is not logged in full."""

import json
import logging

import pytest

from backend.logging_config import log_turn_start, setup_logging

LONG_QUERY = "Rework my Tokyo trip around the canceled flight, " * 2 + "passport X1234567"


@pytest.fixture(autouse=True)
def restore_logging():
    yield
    setup_logging()


def test_backend_module_loggers_follow_the_configured_level_and_log_once(capsys):
    setup_logging(level="INFO", json_output=False)

    logging.getLogger("backend.agents.phase_05_workflow.runner").info("hold released")
    logging.getLogger("backend.routers.chat").debug("too chatty")

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1 and "hold released" in lines[0]


def test_backend_module_loggers_emit_json_when_log_json_is_set(capsys):
    setup_logging(level="INFO", json_output=True)

    logging.getLogger("backend.db.rds_data_client").info("scoped session opened")

    record = json.loads(capsys.readouterr().out.strip())
    assert record["message"] == "scoped session opened"
    assert record["logger"] == "backend.db.rds_data_client"


def test_json_logs_do_not_carry_the_full_traveler_query(capsys):
    setup_logging(level="INFO", json_output=True)

    log_turn_start(5, LONG_QUERY, traveler_id="trv_1")

    out = capsys.readouterr().out
    assert "X1234567" not in out, "the full query must not be written"
    records = [json.loads(line) for line in out.splitlines() if line.strip()]
    queries = [r["query"] for r in records if "query" in r]
    assert queries and all(len(q) <= 61 for q in queries)
    assert all(LONG_QUERY.startswith(q.rstrip("…")) for q in queries)
