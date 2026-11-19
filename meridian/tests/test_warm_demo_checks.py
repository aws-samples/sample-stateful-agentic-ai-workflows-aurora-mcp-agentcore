"""Every warm-up step judges its reply; none passes just because the call returned."""

import pytest

from scripts import warm_demo


def test_every_step_has_a_check():
    assert all(check is not None for _label, _path, _body, check in warm_demo.steps())


@pytest.mark.parametrize(("check", "reply", "passes"), [
    (warm_demo.check_catalog, {"products": [{"product_id": "p1"}], "total": 1}, True),
    (warm_demo.check_catalog, {"products": [], "total": 0}, False),
    (warm_demo.check_catalog, {"packages": [{"package_id": "p1"}]}, False),
    (warm_demo.check_catalog, {"error": "x"}, False),
    (warm_demo.check_profile, {"traveler_id": warm_demo.TRAVELER_ID, "facts": []}, True),
    (warm_demo.check_profile, {"traveler_id": "trv_demo_decoy", "facts": []}, False),
    (warm_demo.check_profile, {"traveler_id": warm_demo.TRAVELER_ID}, False),
    (warm_demo.check_journeys, {"journeys": []}, True),
    (warm_demo.check_journeys, {"journeys": [{"journey_id": "j1"}]}, True),
    (warm_demo.check_journeys, {}, False),
    (warm_demo.check_journeys, {"journeys": "none"}, False),
])
def test_the_data_checks_judge_the_shape_of_the_reply(check, reply, passes):
    assert (check(reply) is None) is passes


ERROR_REPLY = {
    "message": "I encountered an error in Production mode. Please try again.",
    "activities": [{"title": "Concierge error", "activity_type": "error", "telemetry": None}],
}


def test_a_turn_that_fell_back_to_the_error_reply_fails_the_check():
    assert warm_demo.check_turn(ERROR_REPLY) == "failed steps: Concierge error"


def test_a_turn_with_ordinary_activities_and_a_message_passes():
    reply = {"message": "Here are three trips.",
             "activities": [{"title": "Search", "activity_type": "database", "telemetry": None}]}

    assert warm_demo.check_turn(reply) is None


def test_a_failed_telemetry_status_and_an_empty_reply_still_fail():
    failed = {"message": "x", "activities": [{"title": "T", "telemetry": {"status": "failed"}}]}

    assert warm_demo.check_turn(failed) == "failed steps: T"
    assert warm_demo.check_turn({"message": "", "activities": []}) == "empty reply"


def test_the_warm_up_fails_a_step_whose_reply_is_the_error_fallback(monkeypatch):
    monkeypatch.setattr(warm_demo, "steps", lambda traveler: [
        ("Phase 1 SQL", "/api/chat", {"message": "m"}, warm_demo.check_turn)])
    monkeypatch.setattr(warm_demo, "call", lambda *args: ERROR_REPLY)

    passed, failed = warm_demo.run_steps("http://testserver", {}, "trv_meridian_demo")

    assert passed == [] and failed == ["Phase 1 SQL"]


@pytest.mark.parametrize("check", [
    warm_demo.check_health, warm_demo.check_turn, warm_demo.check_catalog,
    warm_demo.check_profile, warm_demo.check_journeys,
])
@pytest.mark.parametrize("reply", [[], [{"message": "hi"}], "text", None])
def test_a_reply_that_is_not_an_object_fails_every_check(check, reply):
    assert check(reply) == warm_demo.NOT_AN_OBJECT


@pytest.mark.parametrize("activities", [None, "none", [None], ["x"]])
def test_a_turn_with_malformed_activities_fails_the_check(activities):
    problem = warm_demo.check_turn({"message": "hi", "activities": activities})

    assert problem == "the activities field is not a list of objects"
