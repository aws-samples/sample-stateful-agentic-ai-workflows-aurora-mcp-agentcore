"""An absent hold row must not contradict a checkpoint that recorded a hold."""

from backend.db.journey_document import _hold_absent


def test_a_journey_without_a_hold_says_none_was_placed() -> None:
    assert _hold_absent({}) == {
        "status": "unavailable",
        "reason": "no hold has been placed for this journey",
    }


def test_a_checkpointed_hold_missing_from_aurora_is_named() -> None:
    absent = _hold_absent({"hold_id": "HLD-0125BCDA"})
    assert absent["status"] == "unavailable"
    assert absent["checkpoint_hold_id"] == "HLD-0125BCDA"
    assert "HLD-0125BCDA" in absent["reason"]
    assert "no hold has been placed" not in absent["reason"]
