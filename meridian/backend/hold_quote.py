"""The terms a traveler saw on the Hold action, and the replies that protect them.

The hold is placed at the quoted unit price. The holds Lambda stays the authority: it replays a
request it already committed, and refuses a new write whose price is not the catalog price
(``hold_price_mismatch``). This module only checks that a quote agrees with the request, and
words the two 409 replies.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

CATALOG_CURRENCY = "USD"
QUOTE_REQUIRED = "quote_required"
PRICE_CHANGED = "price_changed"
PRICE_MISMATCH_ERROR = "hold_price_mismatch"

QUOTE_REQUIRED_MESSAGE = "Review the current price before requesting this hold."
PRICE_CHANGED_MESSAGE = "The price changed after it was shown. Review the new price to continue."


class HoldQuote(BaseModel):
    """The terms displayed on the Hold action when the traveler clicked it."""

    unit_price_cents: int = Field(gt=0, le=100_000_000)
    total_cents: int = Field(gt=0, le=1_200_000_000)
    currency: Literal["USD"] = CATALOG_CURRENCY
    size: str = Field(min_length=1, max_length=50)
    quantity: int = Field(gt=0, le=12)


class HoldQuoteReply(HTTPException):
    """A 409: no hold exists, and ``detail["quote"]`` carries the current terms to approve.

    ``detail`` is ``{"code", "message", "quote"}``. On the wire both routes send ``message`` as
    the plain sentence (``error`` on the plain route, ``message`` on a stream event) beside
    ``code`` and ``quote``.
    """

    def __init__(self, code: str, message: str, quote: dict[str, Any]) -> None:
        super().__init__(status_code=409, detail={"code": code, "message": message, "quote": quote})
        self.code = code

    def body(self) -> dict[str, Any]:
        """The JSON body of the plain route's reply."""
        detail = self.detail
        return {"error": detail["message"], "code": detail["code"], "quote": detail["quote"]}


def current_terms(unit_price: float, size: str, quantity: int) -> dict[str, Any]:
    """The catalog's terms for this request, in cents, as a quote reads."""
    unit_cents = int(round(float(unit_price) * 100))
    return {
        "unit_price_cents": unit_cents,
        "total_cents": unit_cents * quantity,
        "currency": CATALOG_CURRENCY,
        "size": size,
        "quantity": quantity,
    }


def require_agreeing_quote(
    quote: Optional[HoldQuote], current: dict[str, Any]
) -> HoldQuote:
    """The quote, once it is present and agrees with the request it came with.

    ``current`` holds the catalog's terms for the request's duration and party size; its ``size``
    and ``quantity`` are the request's. The quoted price itself is never compared with the
    catalog here: a retry of a committed hold must reach the Lambda that replays it.

    Raises:
        HoldQuoteReply: 409 ``quote_required`` when the client sent no quote.
        HTTPException: 422 when the quote disagrees with the request's duration, party size or
            total.
    """
    if quote is None:
        raise HoldQuoteReply(QUOTE_REQUIRED, QUOTE_REQUIRED_MESSAGE, current)
    if quote.size != current["size"] or quote.quantity != current["quantity"]:
        raise HTTPException(
            status_code=422,
            detail="The quote names a different duration or party size than the request.",
        )
    if quote.total_cents != quote.unit_price_cents * quote.quantity:
        raise HTTPException(
            status_code=422, detail="The quoted total is not the quoted price times the party."
        )
    return quote
