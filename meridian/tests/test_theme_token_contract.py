"""Guard the showcase tooltip colours and the session receipt.

The tooltip once rendered near-black text on a near-black ground in light mode
because its ground was a hard-coded dark hex under themed text. Colour literals
anywhere in the showcase are now rejected by the design-token check
(``frontend/scripts/design-tokens/check.mjs``) and real contrast is measured by
the accessibility suite; this test keeps the tooltip's two colours on role
tokens so both follow the theme together.
"""

from __future__ import annotations

import re
from pathlib import Path

SHOWCASE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "showcase"


def _sheet(name: str) -> str:
    return (SHOWCASE / name).read_text(encoding="utf-8")


def test_tooltip_colours_come_from_role_tokens() -> None:
    """The original bug: themed text over a hard-coded dark ground."""
    css = _sheet("meridianShowcase.css")
    match = re.search(r"\.mds-tooltip\s*\{[^}]*\}", css)
    assert match, ".mds-tooltip rule not found"
    rule = match.group(0)
    assert re.search(r"(?<![-\w])color\s*:\s*var\(--mds-", rule), "tooltip text is not a token"
    assert re.search(r"background\s*:\s*var\(--mds-", rule), "tooltip ground is not a token"
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", rule), "tooltip still carries a hard-coded colour"


# ---------------------------------------------------------------------------
# Session receipt: the closing beat must not overstate what Aurora holds.
# ---------------------------------------------------------------------------


def test_receipt_reads_bookings_as_the_agent_entitled_to_them() -> None:
    """`bookings` is scoped by traveler AND agent type.

    Reading it as ``memory_agent`` returns nothing even when a live hold
    exists, because the policy also gates on ``app.agent_type``. That is the
    policy working, but it made the receipt report zero holds against a real
    reservation.
    """
    source = (
        Path(__file__).resolve().parents[1] / "backend" / "routers" / "diagnostics.py"
    ).read_text(encoding="utf-8")
    receipt = source[source.index("async def session_receipt") :]
    assert 'agent_type="booking_agent"' in receipt, (
        "the bookings count must run under booking_agent or RLS hides the hold"
    )


def test_receipt_is_read_only() -> None:
    """The close must never mutate the state it reports on."""
    source = (
        Path(__file__).resolve().parents[1] / "backend" / "routers" / "diagnostics.py"
    ).read_text(encoding="utf-8")
    receipt = source[source.index("async def session_receipt") :]
    for statement in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE"):
        assert statement not in receipt.upper(), (
            f"session_receipt must stay read-only; found {statement}"
        )
