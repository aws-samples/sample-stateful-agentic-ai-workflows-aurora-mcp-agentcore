"""Every warm-up step judges its reply; none passes just because the call returned."""

import pytest

from scripts import warm_demo


def test_every_step_has_a_check():
    assert all(check is not None for _label, _path, _body, check in warm_demo.steps())


@pytest.mark.parametrize(("check", "reply", "passes"), [
    (warm_demo.check_catalog, {"packages": [{"package_id": "p1"}], "total": 1}, True),
    (warm_demo.check_catalog, {"packages": [], "total": 0}, False),
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
