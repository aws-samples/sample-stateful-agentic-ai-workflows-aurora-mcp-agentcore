"""What a failed hold or booking request knows about its write.

The concierge raises one of these when a hold or booking request fails, so the route can answer
with what is known instead of one generic failure:

  * ``PlacedWriteUnfinished``: the Runtime reported the write placed, then a later step failed.
  * ``WriteOutcomeUnknown``: the invoke may have reached the Runtime, but nothing it reported
    says whether the write was placed.

Any other error means the write was refused or the request never ran.
"""

from fastapi import HTTPException

# The body code that tells the app a 5xx was worded by the route, not by a proxy in front of it.
GOVERNED_WRITE_CODE = "governed_write"

# The operation that places each governed write, and the decision field that reports it.
GOVERNED_WRITES = {"production_hold": "hold", "production_booking": "booking"}

# Each step that can fail after a placed write, in the traveler's words. The route builds its
# reply from these; a step must be listed here to be raised.
STEP_WORDS = {
    "reply": "the concierge's reply",
    "trace": "showing this request's trace",
    "audit": "recording this request in the audit log",
    "memory": "saving this conversation",
}


class PlacedWriteUnfinished(Exception):
    """A hold or booking was placed, then a later step of the same request failed.

    The write is in Aurora, so the caller can say it happened instead of answering as if it
    had not. The step's own error is the ``__cause__``.

    Attributes:
        write: ``"hold"`` or ``"booking"``.
        step: A key of ``STEP_WORDS``: ``"reply"`` (the Runtime's turn failed after it
            reported the write), ``"trace"`` (showing the turn's spans), ``"audit"`` (writing
            its audit row) or ``"memory"`` (storing the turn in conversation memory).
    """

    def __init__(self, write: str, step: str) -> None:
        if step not in STEP_WORDS:
            raise ValueError(f"unknown step after a placed write: {step!r}")
        super().__init__(f"the {write} was placed; the {step} step after it failed")
        self.write = write
        self.step = step


class WriteOutcomeUnknown(Exception):
    """The invoke may have reached the Runtime, and nothing it reported settles the write.

    For example a timeout or a broken connection after the request was sent, or a stream that
    broke before the Runtime reported the hold or booking. The error is the ``__cause__``.

    Attributes:
        write: ``"hold"`` or ``"booking"``.
    """

    def __init__(self, write: str) -> None:
        super().__init__(f"whether the {write} was placed is not known")
        self.write = write


class GovernedWriteReply(HTTPException):
    """The 503 for a failed hold or booking request, worded by what the route knows.

    The app shows its text as written; ``code`` is how it tells this reply from an error page
    a proxy produced.
    """

    code = GOVERNED_WRITE_CODE

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=503, detail=detail)
