"""Records of the identity proof: one Outcome per probe, one Receipt per run.

A receipt holds no token, no password and no account id; ``leaks`` is the check that says so.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from scripts.prove_backend_login import ACCOUNT_ID, AWS_KEY_ID, LONG_SECRET, TOKEN, mask

SCHEMA = 1
LAYERS = ("backend", "runtime", "gateway", "database")
DECOY, JORDAN = "decoy", "jordan"
REFUSED, ALLOWED, ERROR = "refused", "allowed", "error"
NOT_ATTRIBUTED = "not_attributed"
FULL, JORDAN_ONLY = "full", "jordan-only"
DETAIL_LIMIT = 160
REFUSER_LABELS = {
    "backend_identity_check": "Backend: token traveler check",
    "workload_grant": "Workload grant on the traveler",
    "runtime_traveler_check": "Runtime: token claim check",
    "runtime_token_check": "Runtime: token check",
    "gateway_cedar": "Gateway: Cedar policy",
    "gateway_interceptor": "Gateway: interceptor",
    "gateway_workload_grant": "Holds Lambda: workload grant",
    "database_rls": "AWS Aurora: row-level security",
    NOT_ATTRIBUTED: "Refused, layer not attributed",
}
LEAK_PATTERNS = {
    "token": TOKEN,
    "account id": ACCOUNT_ID,
    "access key id": AWS_KEY_ID,
    "long secret": LONG_SECRET,
}
TABLE_HEAD = ("LAYER", "PROBE", "ACTOR", "EXPECTED", "RESULT", "REFUSED BY", "EVIDENCE")
TABLE_WIDTHS = (9, 42, 6, 9, 8, 34, 44)


def scrub(text: object, limit: int = DETAIL_LIMIT) -> str:
    """One line of ``text`` with tokens, key ids, long secrets and account ids masked."""
    return mask(" ".join(str(text).split()))[:limit]


def leaks(text: str) -> list[str]:
    """The kinds of secret found in ``text``; empty when it is clean."""
    return [name for name, pattern in LEAK_PATTERNS.items() if pattern.search(text)]


@dataclass(frozen=True)
class Outcome:
    """What one probe sent as one user and which layer answered.

    Attributes:
        probe: Stable id such as ``backend.decoy_reads_jordan_memory``.
        layer: One of ``LAYERS``.
        actor: ``decoy`` or ``jordan``.
        expected: ``refused`` or ``allowed``.
        result: ``refused``, ``allowed`` or ``error``.
        refused_by: Key of ``REFUSER_LABELS`` when refused, else None.
        detail: A scrubbed line of what came back.
        evidence: Small scalars (a status, a count, an audit delta).
        millis: How long the probe took.
        at: ISO time the probe finished.
    """

    probe: str
    layer: str
    actor: str
    expected: str
    result: str
    refused_by: str | None
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    millis: int = 0
    at: str = ""

    @property
    def passed(self) -> bool:
        """True when the result is the expectation and a refusal names its refuser."""
        if self.result != self.expected:
            return False
        return self.expected != REFUSED or bool(self.refused_by)


@dataclass
class Receipt:
    """One run of the proof."""

    at: str
    git_sha: str
    region: str
    pool_suffix: str
    design: str
    site_host: str
    mode: str = FULL
    outcomes: list[Outcome] = field(default_factory=list)
    cleanup: dict[str, Any] = field(default_factory=dict)
    account: str = "<acct>"

    def coverage_gaps(self) -> list[str]:
        """Layers that lack a decoy refusal probe (full mode) or a Jordan control."""
        gaps = []
        for layer in LAYERS:
            mine = [o for o in self.outcomes if o.layer == layer]
            if self.mode == FULL and not any(
                    o.actor == DECOY and o.expected == REFUSED for o in mine):
                gaps.append(f"{layer}: no decoy refusal probe ran")
            if not any(o.actor == JORDAN and o.expected == ALLOWED for o in mine):
                gaps.append(f"{layer}: no Jordan control ran")
        return gaps

    @property
    def ok(self) -> bool:
        """True when every probe passed, every layer is covered and nothing was left behind."""
        return (not self.coverage_gaps() and all(o.passed for o in self.outcomes)
                and not self.cleanup.get("leftovers"))

    def to_dict(self) -> dict[str, Any]:
        """The receipt as plain data, ready for JSON."""
        return {
            "schema": SCHEMA, "at": self.at, "git_sha": self.git_sha, "region": self.region,
            "account": self.account, "pool_suffix": self.pool_suffix, "design": self.design,
            "site_host": self.site_host, "mode": self.mode, "ok": self.ok,
            "coverage_gaps": self.coverage_gaps(),
            "outcomes": [{**asdict(o), "passed": o.passed} for o in self.outcomes],
            "summary": summary_rows(self), "cleanup": self.cleanup,
        }


def _verdict(outcomes: list[Outcome], wanted: str) -> str:
    relevant = [o for o in outcomes if o.expected == wanted]
    if not relevant:
        return "not run"
    return wanted if all(o.passed for o in relevant) else "FAILED"


def summary_rows(receipt: Receipt) -> list[dict[str, str]]:
    """One row per layer: how the decoy fared, how Jordan fared and who refused the decoy."""
    rows = []
    for layer in LAYERS:
        mine = [o for o in receipt.outcomes if o.layer == layer]
        decoy = [o for o in mine if o.actor == DECOY]
        jordan = [o for o in mine if o.actor == JORDAN]
        refusers = sorted({REFUSER_LABELS.get(o.refused_by or "", "") for o in decoy
                           if o.refused_by})
        rows.append({
            "layer": layer, "decoy": _verdict(decoy, REFUSED), "jordan": _verdict(jordan, ALLOWED),
            "refused_by": "; ".join(refusers),
        })
    return rows


def _line(cells) -> str:
    return "  ".join(str(cell)[:width].ljust(width)
                     for cell, width in zip(cells, TABLE_WIDTHS, strict=True)).rstrip()


def render_table(receipt: Receipt) -> str:
    """The fixed-width table the command prints, ending in ``RESULT: PASS`` or ``RESULT: FAIL``."""
    lines = [_line(TABLE_HEAD), _line("-" * width for width in TABLE_WIDTHS)]
    for o in receipt.outcomes:
        lines.append(_line((o.layer, o.probe, o.actor, o.expected, o.result,
                            REFUSER_LABELS.get(o.refused_by or "", ""), o.detail)))
    lines.extend(f"GAP: {gap}" for gap in receipt.coverage_gaps())
    for problem in receipt.cleanup.get("problems", []):
        lines.append(f"CLEANUP: {problem}")
    lines.append(f"RESULT: {'PASS' if receipt.ok else 'FAIL'}")
    return "\n".join(lines) + "\n"


def write_receipt(path: Path, receipt: Receipt) -> None:
    """Write the receipt as JSON, mode 0600, through a temporary file renamed over ``path``.

    Raises:
        ValueError: When the serialized receipt would hold a token, key id, long secret or
            account id; nothing is written.
    """
    text = json.dumps(receipt.to_dict(), indent=2) + "\n"
    found = leaks(text)
    if found:
        raise ValueError(f"the receipt would contain {', '.join(found)}; it was not written")
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    try:
        descriptor = os.open(
            scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(scratch, path)
    finally:
        scratch.unlink(missing_ok=True)
