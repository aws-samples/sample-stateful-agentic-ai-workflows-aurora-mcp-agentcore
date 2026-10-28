"""The authenticated hosted warm-up records its result in the release record."""
import json

import pytest

from scripts import published, warm_demo

REPLIES = {
    "/api/health": {"status": "healthy", "aurora_reachable": True},
    "/api/products?limit=50": {"products": [{"product_id": "p1"}]},
    f"/api/memory/{warm_demo.TRAVELER_ID}": {"traveler_id": warm_demo.TRAVELER_ID, "facts": []},
    "/api/journeys?limit=5": {"journeys": []},
}
TURN = {"message": "A reply.", "activities": []}
BEFORE_THIS_CHANGE = {
    "startedAt": "2026-10-27T10:00:00+00:00", "account": "123456789012", "region": "us-east-1",
    "serviceArn": "arn:aws:apprunner:us-east-1:123456789012:service/meridian-web/abc123",
    "status": "deployed_pending_verification", "identityMode": "jwt",
    "previousImage": "old-image", "image": "new-image",
    "site": {"SiteUrl": "https://example.com/"},
    "finishedAt": "2026-10-27T10:20:00+00:00",
}


@pytest.fixture
def record_path(tmp_path, monkeypatch):
    path = tmp_path / ".local" / "hosted-release.json"
    path.parent.mkdir()
    path.write_text(json.dumps(BEFORE_THIS_CHANGE))
    monkeypatch.setattr(published, "RECORD", path)
    monkeypatch.setattr(published, "head_commit", lambda: "c0ffee")
    monkeypatch.setenv("MERIDIAN_HOSTED_AUTH", '{"username": "u", "password": "test-only"}')
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)
    return path


def serve(monkeypatch, overrides=None):
    replies = {**REPLIES, **(overrides or {})}

    def fake_call(_base, _headers, path, body=None):
        reply = replies.get(path, TURN if body else {})
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(warm_demo, "call", fake_call)


def test_a_full_pass_marks_the_record_verified(record_path, monkeypatch):
    serve(monkeypatch)
    assert warm_demo.main(["--hosted", "--verify"]) == 0
    record = json.loads(record_path.read_text())
    assert record["status"] == "verified"
    assert record["verifiedCommit"] == "c0ffee"
    assert record["verifiedAt"]
    assert record["checks"] == [label for label, *_ in warm_demo.steps()]
    assert "failedCheck" not in record


def test_a_failing_check_marks_the_record_failed_and_exits_nonzero(record_path, monkeypatch):
    serve(monkeypatch, {"/api/products?limit=50": {"products": []}})
    assert warm_demo.main(["--hosted", "--verify"]) == 1
    record = json.loads(record_path.read_text())
    assert record["status"] == "verification_failed"
    assert record["failedCheck"] == "Catalog"
    assert "Catalog" not in record["checks"]
    assert "verifiedAt" not in record


def test_an_unhealthy_backend_is_recorded_as_the_failed_check(record_path, monkeypatch):
    serve(monkeypatch, {"/api/health": ValueError("no answer")})
    assert warm_demo.main(["--hosted", "--verify"]) == 1
    record = json.loads(record_path.read_text())
    assert (record["status"], record["failedCheck"], record["checks"]) == (
        "verification_failed", "Health and Aurora", [])


def test_a_record_from_before_this_change_keeps_its_fields(record_path, monkeypatch):
    serve(monkeypatch)
    warm_demo.main(["--hosted", "--verify"])
    record = json.loads(record_path.read_text())
    kept = {key: value for key, value in record.items() if key in BEFORE_THIS_CHANGE}
    assert kept == {**BEFORE_THIS_CHANGE, "status": "verified"}
    assert set(record) - set(BEFORE_THIS_CHANGE) == {"checks", "verifiedAt", "verifiedCommit"}


def test_a_later_pass_clears_an_earlier_failure(record_path, monkeypatch):
    serve(monkeypatch, {"/api/journeys?limit=5": {}})
    warm_demo.main(["--hosted", "--verify"])
    serve(monkeypatch)
    warm_demo.main(["--hosted", "--verify"])
    record = json.loads(record_path.read_text())
    assert record["status"] == "verified"
    assert "failedCheck" not in record


def test_the_record_never_holds_credentials(record_path, monkeypatch):
    serve(monkeypatch)
    warm_demo.main(["--hosted", "--verify"])
    assert "test-only" not in record_path.read_text()


def test_an_unfinished_release_is_not_verified(record_path, monkeypatch):
    record_path.write_text(json.dumps({**BEFORE_THIS_CHANGE, "status": "in_progress"}))
    serve(monkeypatch)
    with pytest.raises(SystemExit, match="in_progress"):
        warm_demo.main(["--hosted", "--verify"])
    assert json.loads(record_path.read_text())["status"] == "in_progress"


def test_without_verify_the_record_is_untouched(record_path, monkeypatch):
    serve(monkeypatch)
    assert warm_demo.main(["--hosted"]) == 0
    assert json.loads(record_path.read_text()) == BEFORE_THIS_CHANGE


def test_verify_needs_the_hosted_target(record_path, monkeypatch):
    serve(monkeypatch)
    with pytest.raises(SystemExit) as exit_info:
        warm_demo.main(["--verify"])
    assert exit_info.value.code == 2
    assert json.loads(record_path.read_text()) == BEFORE_THIS_CHANGE


@pytest.mark.parametrize("error", [OSError("connection reset"), TimeoutError("slow"),
                                   ConnectionResetError("reset")])
def test_a_transport_error_midway_is_recorded_as_the_failed_check(record_path, monkeypatch, error):
    serve(monkeypatch, {"/api/products?limit=50": error})
    assert warm_demo.main(["--hosted", "--verify"]) == 1
    record = json.loads(record_path.read_text())
    assert (record["status"], record["failedCheck"]) == ("verification_failed", "Catalog")
    assert record["checks"] == ["Health and Aurora"] + [
        label for label, *_ in warm_demo.steps()][2:]


def test_verify_as_another_traveler_is_refused_and_writes_nothing(record_path, monkeypatch):
    serve(monkeypatch)
    with pytest.raises(SystemExit) as exit_info:
        warm_demo.main(["--hosted", "--verify", "--as", "decoy"])
    assert exit_info.value.code == 2
    assert json.loads(record_path.read_text()) == BEFORE_THIS_CHANGE
