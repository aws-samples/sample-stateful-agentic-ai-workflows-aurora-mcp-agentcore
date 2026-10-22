"""Hold intent: allocated once, checkpointed, then executed.

The database constraint on (journey_id, hold_request_id) is the second line of
defence. The first is that the identity is decided and durably checkpointed
before the business action runs, so every retry and every later execution
reuses it instead of allocating a new one.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional

from backend.agents.phase_05_workflow.packages import (
    duration_for_party,
    find_ranked_package,
    ranked_package_id,
    top_ranked_package,
)

DEFAULT_QUANTITY = 1


@dataclass
class HoldIntent:
    """A hold that has been decided but not yet performed."""

    hold_request_id: str
    package_id: str
    duration: str
    quantity: int
    unit_price: Decimal
    total_amount: Decimal
    fingerprint: str


def normalize_hold_terms(
    package_id: str, duration: str, quantity: int, unit_price: Decimal
) -> dict:
    """Validate the material terms of a hold and reduce them to comparable form.

    Args:
        package_id: Catalog package identifier.
        duration: Duration label as it appears in the catalog.
        quantity: Number of travelers. Must be positive.
        unit_price: Price per traveler. Must not be negative.

    Returns:
        Terms in the form used for comparison only. Case and surrounding
        whitespace are incidental, so the same intent expressed two ways
        compares alike. These are not the terms that get booked: the booked
        package id keeps the caller's casing, because it is a catalog key.

    Raises:
        ValueError: If any term is missing or out of range. The fingerprint is
            computed from validated terms only, so it never encodes unchecked
            input.
    """
    if not package_id or not package_id.strip():
        raise ValueError("package_id is required")
    if not duration or not duration.strip():
        raise ValueError("duration is required")
    if quantity is None or quantity <= 0:
        raise ValueError("quantity must be positive")
    if unit_price is None or Decimal(unit_price) < 0:
        raise ValueError("unit price must not be negative")

    price = Decimal(unit_price).quantize(Decimal("0.01"))
    return {
        "package_id": package_id.strip().lower(),
        "duration": " ".join(duration.split()).lower(),
        "quantity": int(quantity),
        "unit_price": str(price),
        "total_amount": str(price * int(quantity)),
    }


def fingerprint_terms(terms: dict) -> str:
    """Return a stable digest of normalized terms."""
    canonical = json.dumps(terms, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_hold_intent(
    package_id: str, duration: str, quantity: int, unit_price: Decimal
) -> HoldIntent:
    """Allocate a hold request identity over validated terms.

    The intent carries the terms as they will be booked. Only the fingerprint
    is computed over the case-folded form, so two spellings of one intent are
    recognized as the same hold without either spelling reaching the catalog.
    """
    terms = normalize_hold_terms(package_id, duration, quantity, unit_price)
    return HoldIntent(
        hold_request_id=f"hrq_{uuid.uuid4().hex[:12]}",
        package_id=package_id.strip(),
        duration=" ".join(duration.split()),
        quantity=terms["quantity"],
        unit_price=Decimal(terms["unit_price"]),
        total_amount=Decimal(terms["total_amount"]),
        fingerprint=fingerprint_terms(terms),
    )


def _hold_target(state: dict) -> Optional[Dict[str, Any]]:
    """The ranked package the hold is for.

    The package the traveler chose at review (``selected_package``) wins; it is
    validated against the reviewed shortlist before the run resumes. Otherwise the
    top-ranked package, read exactly as the hold node reads it, so the
    fingerprinted hold and the executed hold are the same hold.
    """
    chosen = state.get("selected_package")
    if chosen:
        return find_ranked_package(state.get("packages"), str(chosen))
    return top_ranked_package(state.get("packages"))


def _travelers(quantity: int) -> str:
    return f"{quantity} traveler" if quantity == 1 else f"{quantity} travelers"


def prepare_hold_node(state: dict) -> Dict[str, Any]:
    """Graph node that establishes the hold intent before the hold runs.

    Returns the existing intent unchanged when the state already carries one,
    so a resumed graph replays with the identity it checkpointed rather than
    allocating a second one. Returns nothing when there is no ranked option,
    matching the hold node, which skips rather than fails in that case. When the
    package counts its places and no duration seats the whole party, returns
    ``hold_unavailable`` with the reason instead of an intent the write would refuse.
    """
    existing = state.get("hold_intent")
    if existing:
        return {"hold_intent": existing}

    target = _hold_target(state)
    if not target:
        chosen = state.get("selected_package")
        return {"hold_unavailable": (
            f"The chosen package {chosen} is not among the ranked options."
        )} if chosen else {}

    package_id = ranked_package_id(target)
    quantity = int(state.get("travelers_count") or DEFAULT_QUANTITY)
    duration = duration_for_party(target, quantity)
    if duration is None:
        return {
            "hold_unavailable": (
                f"No duration of {package_id} has places left for {_travelers(quantity)}."
            )
        }

    unit_price = Decimal(str(target.get("price") or 0))
    intent = build_hold_intent(package_id, duration, quantity, unit_price)
    return {
        "hold_intent": {
            "hold_request_id": intent.hold_request_id,
            "booking_id": f"hold_{intent.hold_request_id.removeprefix('hrq_')}",
            "package_id": intent.package_id,
            "duration": intent.duration,
            "quantity": intent.quantity,
            "unit_price": str(intent.unit_price),
            "total_amount": str(intent.total_amount),
            "fingerprint": intent.fingerprint,
        }
    }
