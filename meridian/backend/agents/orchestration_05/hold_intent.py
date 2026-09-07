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

from backend.agents.orchestration_05.packages import (
    first_available_duration,
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


def _terms_from_state(state: dict) -> Optional[tuple]:
    """Read the hold's terms from workflow state.

    An explicitly selected package wins. Otherwise the terms come from the
    ranked list exactly as the hold node reads it, so the fingerprinted hold
    and the executed hold are the same hold.

    Returns:
        A ``(package_id, duration, quantity, unit_price)`` tuple, or None when
        the ranking is empty and there is nothing to hold.
    """
    quantity = int(state.get("travelers_count") or DEFAULT_QUANTITY)

    selected = state.get("selected_package")
    if selected:
        return (
            str(selected),
            str(state.get("duration") or ""),
            quantity,
            Decimal(str(state.get("unit_price") or 0)),
        )

    target = top_ranked_package(state.get("packages"))
    if not target:
        return None

    return (
        str(target.get("product_id") or target.get("package_id")),
        first_available_duration(target),
        quantity,
        Decimal(str(target.get("price") or 0)),
    )


def prepare_hold_node(state: dict) -> Dict[str, Any]:
    """Graph node that establishes the hold intent before the hold runs.

    Returns the existing intent unchanged when the state already carries one,
    so a resumed graph replays with the identity it checkpointed rather than
    allocating a second one. Returns nothing when there is no ranked option,
    matching the hold node, which skips rather than fails in that case.
    """
    existing = state.get("hold_intent")
    if existing:
        return {"hold_intent": existing}

    terms = _terms_from_state(state)
    if terms is None:
        return {}

    intent = build_hold_intent(*terms)
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
