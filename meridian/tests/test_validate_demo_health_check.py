"""The hosted rehearsal health check expects the store the app reports today."""

import pytest

from backend.agents.phase_05_workflow.state import SNAPSHOT_STORE
from scripts import validate_demo


def test_health_check_accepts_the_snapshot_store_the_app_reports():
    body = {"checkpoint_backend": SNAPSHOT_STORE, "checkpoint_durable": True}
    assert validate_demo.reports_durable_snapshots(body, 200)


def test_health_check_rejects_the_retired_saver_name():
    body = {"checkpoint_backend": "AuroraDataApiSaver", "checkpoint_durable": True}
    assert not validate_demo.reports_durable_snapshots(body, 200)


def test_health_check_requires_the_store_to_be_durable():
    body = {"checkpoint_backend": SNAPSHOT_STORE, "checkpoint_durable": False}
    assert not validate_demo.reports_durable_snapshots(body, 200)


def test_health_check_follows_snapshot_store_instead_of_a_literal(monkeypatch):
    monkeypatch.setattr(validate_demo, "SNAPSHOT_STORE", "another store")
    body = {"checkpoint_backend": "another store", "checkpoint_durable": True}
    assert validate_demo.reports_durable_snapshots(body, 200)
    assert not validate_demo.reports_durable_snapshots(
        {"checkpoint_backend": SNAPSHOT_STORE, "checkpoint_durable": True}, 200
    )


def test_receipt_rows_check_requires_a_positive_sum():
    assert validate_demo.receipt_rows_counted({"lines": [{"count": 0}, {"count": 3}]}, 200)
    assert not validate_demo.receipt_rows_counted({"lines": [{"count": 0}]}, 200)


def test_receipt_rows_check_names_the_unavailable_line():
    body = {"lines": [{"label": "Courtesy holds still live", "count": None}, {"count": 5}]}
    with pytest.raises(ValueError, match="Courtesy holds still live.*could not be counted"):
        validate_demo.receipt_rows_counted(body, 200)
