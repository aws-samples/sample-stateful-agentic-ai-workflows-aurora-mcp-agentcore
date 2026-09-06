"""The hold intent must be stable across retries and executions.

The hold_requests row commits with the booking, so it cannot be the record of
intent: if the transaction rolls back, or the worker dies first, it does not
exist. The checkpointed intent is what survives.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.agents.orchestration_05.hold_intent import (
    HoldIntent,
    build_hold_intent,
    fingerprint_terms,
    normalize_hold_terms,
    prepare_hold_node,
)


# ------------------------------------------------------------- fingerprint


def test_fingerprint_is_stable_for_identical_terms() -> None:
    terms = normalize_hold_terms("TKY-003", "3 nights", 2, Decimal("1949.00"))
    assert fingerprint_terms(terms) == fingerprint_terms(dict(terms))


def test_fingerprint_changes_with_quantity() -> None:
    a = fingerprint_terms(normalize_hold_terms("TKY-003", "3 nights", 2, Decimal("1949")))
    b = fingerprint_terms(normalize_hold_terms("TKY-003", "3 nights", 3, Decimal("1949")))
    assert a != b


def test_fingerprint_changes_with_price() -> None:
    a = fingerprint_terms(normalize_hold_terms("TKY-003", "3 nights", 2, Decimal("1949")))
    b = fingerprint_terms(normalize_hold_terms("TKY-003", "3 nights", 2, Decimal("2049")))
    assert a != b


def test_fingerprint_changes_with_package() -> None:
    a = fingerprint_terms(normalize_hold_terms("TKY-003", "3 nights", 2, Decimal("1949")))
    b = fingerprint_terms(normalize_hold_terms("TKY-005", "3 nights", 2, Decimal("1949")))
    assert a != b


def test_normalization_is_insensitive_to_incidental_formatting() -> None:
    a = normalize_hold_terms("TKY-003", "3 Nights ", 2, Decimal("1949.00"))
    b = normalize_hold_terms("tky-003", "3 nights", 2, Decimal("1949"))
    assert fingerprint_terms(a) == fingerprint_terms(b)


# --------------------------------------------------------------- validation


def test_quantity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="quantity must be positive"):
        normalize_hold_terms("TKY-003", "3 nights", 0, Decimal("1949"))


def test_price_must_not_be_negative() -> None:
    with pytest.raises(ValueError, match="unit price must not be negative"):
        normalize_hold_terms("TKY-003", "3 nights", 1, Decimal("-1"))


def test_package_id_is_required() -> None:
    with pytest.raises(ValueError, match="package_id is required"):
        normalize_hold_terms("  ", "3 nights", 1, Decimal("1949"))


def test_duration_is_required() -> None:
    with pytest.raises(ValueError, match="duration is required"):
        normalize_hold_terms("TKY-003", " ", 1, Decimal("1949"))


# ------------------------------------------------------------------ intent


def test_total_is_derived_not_supplied() -> None:
    intent = build_hold_intent("TKY-003", "3 nights", 2, Decimal("1949.00"))
    assert intent.total_amount == Decimal("3898.00")


def test_the_intent_carries_the_package_id_as_it_will_be_booked() -> None:
    """Case folding belongs to the fingerprint, not to the booked terms.

    `create_courtesy_hold` writes `p_package_id` into the booking, so a
    lowercased id would persist a catalog key that does not exist.
    """
    intent = build_hold_intent("TKY-003", "3 Nights", 2, Decimal("1949.00"))
    assert intent.package_id == "TKY-003"
    assert intent.duration == "3 Nights"


def test_request_id_is_allocated() -> None:
    intent = build_hold_intent("TKY-003", "3 nights", 1, Decimal("1949"))
    assert intent.hold_request_id.startswith("hrq_")


def test_two_intents_get_different_request_ids() -> None:
    a = build_hold_intent("TKY-003", "3 nights", 1, Decimal("1949"))
    b = build_hold_intent("TKY-003", "3 nights", 1, Decimal("1949"))
    assert a.hold_request_id != b.hold_request_id


def test_differently_cased_intents_still_share_a_fingerprint() -> None:
    a = build_hold_intent("TKY-003", "3 Nights", 2, Decimal("1949.00"))
    b = build_hold_intent("tky-003", "3 nights", 2, Decimal("1949"))
    assert a.fingerprint == b.fingerprint


def test_intent_is_a_value_object() -> None:
    intent = HoldIntent(
        hold_request_id="hrq_1",
        package_id="TKY-003",
        duration="3 nights",
        quantity=1,
        unit_price=Decimal("1949"),
        total_amount=Decimal("1949"),
        fingerprint="abc",
    )
    assert intent.quantity == 1


# -------------------------------------------------------------------- node


def _state(**overrides) -> dict:
    state = {
        "packages": [
            {
                "product_id": "TKY-003",
                "price": 1949.00,
                "availability": {"3 nights": 4, "7 nights": 0},
            }
        ],
        "travelers_count": 2,
    }
    state.update(overrides)
    return state


def test_prepare_node_writes_the_intent_into_state() -> None:
    result = prepare_hold_node(_state())
    assert result["hold_intent"]["hold_request_id"].startswith("hrq_")
    assert result["hold_intent"]["fingerprint"]


def test_prepare_node_describes_the_hold_that_will_actually_run() -> None:
    """The intent must be over the terms the hold node holds, not other terms.

    The hold node picks the top-ranked package and the first duration that
    still has inventory. An intent derived from anything else would fingerprint
    a hold that never happens.
    """
    result = prepare_hold_node(_state())
    intent = result["hold_intent"]
    assert intent["package_id"] == "TKY-003"
    assert intent["duration"] == "3 nights"
    assert intent["quantity"] == 2
    assert intent["unit_price"] == "1949.00"
    assert intent["total_amount"] == "3898.00"


def test_prepare_node_is_idempotent_on_resume() -> None:
    state = _state()
    first = prepare_hold_node(state)
    resumed = dict(state, **first)
    second = prepare_hold_node(resumed)
    assert (
        second["hold_intent"]["hold_request_id"]
        == first["hold_intent"]["hold_request_id"]
    ), "a resumed graph must reuse the checkpointed identity, not allocate a new one"


def test_prepare_node_yields_nothing_when_there_is_nothing_to_hold() -> None:
    """The hold node already skips an empty ranking; preparing must not raise."""
    assert prepare_hold_node(_state(packages=[])) == {}


def test_prepare_node_accepts_an_explicitly_selected_package() -> None:
    result = prepare_hold_node(
        _state(selected_package="TKY-005", duration="5 nights", unit_price="2100.00")
    )
    intent = result["hold_intent"]
    assert intent["package_id"] == "TKY-005"
    assert intent["duration"] == "5 nights"
    assert intent["unit_price"] == "2100.00"


# ------------------------------------------------------------------- graph


def test_the_hold_node_receives_a_checkpointed_intent() -> None:
    """The intent must be in state before the hold node runs, not derived in it.

    A hold node that re-derives its own terms can disagree with the intent
    that was fingerprinted, which would defeat the idempotency the
    hold_requests key is meant to provide.
    """
    import asyncio

    from backend.agents.orchestration_05.workflow import OrchestrationAgent

    async def fake_search(q: str, limit: int = 5):
        return ([{"product_id": "TKY-003", "name": "Tokyo", "price": 1949}], [])

    async def fake_avail(q: str, package_id: str | None = None):
        return (
            [
                {
                    "product_id": package_id or "TKY-003",
                    "price": 1949,
                    "availability": {"2 nights": 14, "3 nights": 0},
                }
            ],
            [],
            "",
        )

    seen: list[dict] = []

    async def fake_hold(state):
        seen.append(dict(state))
        return {"activities": list(state.get("activities", []))}

    workflow = OrchestrationAgent(search_fn=fake_search, availability_fn=fake_avail)
    workflow._node_hold = fake_hold  # type: ignore[method-assign]

    asyncio.run(
        workflow.run(
            "My flight was cancelled, rework the trip and show duration availability.",
            traveler_id="trv_meridian_demo",
            conversation_id="hold-intent-graph",
        )
    )

    assert len(seen) == 1, "the plan path must reach the hold node exactly once"
    intent = seen[0].get("hold_intent")
    assert intent, "prepare_hold must run before hold"
    assert intent["package_id"] == "TKY-003"
    assert intent["duration"] == "2 nights", "the sold-out duration must not be held"
    assert intent["hold_request_id"].startswith("hrq_")
