"""Records of the identity proof: one Outcome per probe, one Receipt per run.

A receipt holds no token, no password and no account id; ``leaks`` is the check that says so.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from scripts.prove_backend_login import ACCOUNT_ID, AWS_KEY_ID, LONG_SECRET, TOKEN, mask

SCHEMA = 1
LAYERS = ("backend", "runtime", "gateway", "database")
DECOY, JORDAN = "decoy", "jordan"
REFUSED, ALLOWED, ERROR = "refused", "allowed", "error"
FULL, JORDAN_ONLY = "full", "jordan-only"
UNPROVEN = "unproven"
DETAIL_LIMIT = 160
REFUSER_LABELS = {
    "backend_identity_check": "Backend: token traveler check",
    "workload_grant": "Workload grant on the traveler",
    "runtime_traveler_check": "Runtime: token claim check",
    "gateway_cedar": "Gateway: Cedar policy",
    "gateway_interceptor": "Gateway: interceptor",
    "gateway_workload_grant": "Holds Lambda: workload grant",
    "database_rls": "AWS Aurora: row-level security",
}
POOL_ID = re.compile(r"\b[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]{9}\b")
CLIENT_ID = re.compile(
    r"(?<![A-Za-z0-9_-])(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{26}(?![A-Za-z0-9_-])")
ARN = re.compile(r"\barn:aws[a-z-]*:[A-Za-z0-9-]*:[A-Za-z0-9-]*:[A-Za-z0-9<>*-]*:[^\s\"',;]+")
LEAK_PATTERNS = {
    "token": TOKEN,
    "account id": ACCOUNT_ID,
    "access key id": AWS_KEY_ID,
    "long secret": LONG_SECRET,
    "pool id": POOL_ID,
    "client id": CLIENT_ID,
    "arn": ARN,
}
TABLE_HEAD = ("LAYER", "PROBE", "ACTOR", "EXPECTED", "RESULT", "REFUSED BY", "EVIDENCE")
TABLE_WIDTHS = (9, 42, 6, 9, 8, 34, 44)


def mask_text(text: str) -> str:
    """``text`` with ARNs, pool and client ids, tokens, key ids, secrets and accounts masked."""
    text = ARN.sub("<arn>", text)
    text = POOL_ID.sub("<pool-id>", text)
    return mask(CLIENT_ID.sub("<client-id>", text))


def scrub(text: object, limit: int = DETAIL_LIMIT) -> str:
    """One masked line of ``text``, cut to ``limit`` characters."""
    return mask_text(" ".join(str(text).split()))[:limit]


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
        """True when the result is the expectation and a refusal names a known refuser.

        A refusal that cannot be attributed does not pass: it is not evidence of any layer.
        """
        if self.result != self.expected:
            return False
        return self.expected != REFUSED or self.refused_by in REFUSER_LABELS


@dataclass
class Receipt:
    """One run of the proof."""

    at: str
    git_sha: str
    region: str
    design: str
    site_host: str
    mode: str = FULL
    outcomes: list[Outcome] = field(default_factory=list)
    cleanup: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    account: str = "<acct>"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Receipt:
        """Rebuild a receipt from its JSON form; every verdict is recomputed from the outcomes.

        Raises:
            KeyError: When a required field is missing.
            TypeError: When a field has the wrong type.
            ValueError: When the schema or mode is not one this code wrote.
        """
        if data["schema"] != SCHEMA:
            raise ValueError(f"receipt schema {data['schema']!r} is not {SCHEMA}")
        if data["mode"] not in (FULL, JORDAN_ONLY):
            raise ValueError(f"receipt mode {data['mode']!r} is not known")
        names = {f.name for f in fields(Outcome)}
        outcomes = [Outcome(**{k: v for k, v in raw.items() if k in names})
                    for raw in data["outcomes"]]
        return cls(
            at=data["at"], git_sha=data["git_sha"], region=data["region"], design=data["design"],
            site_host=data["site_host"], mode=data["mode"], outcomes=outcomes,
            cleanup=dict(data["cleanup"]), notes=list(data.get("notes", [])))

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
        """True when every probe passed, every layer is covered and cleanup was complete.

        A leftover fails the receipt, and so does any cleanup problem: a step that could not run
        leaves a state nobody has checked.
        """
        return (not self.coverage_gaps() and all(o.passed for o in self.outcomes)
                and not self.cleanup.get("leftovers") and not self.cleanup.get("problems"))

    def to_dict(self) -> dict[str, Any]:
        """The receipt as plain data, ready for JSON."""
        return {
            "schema": SCHEMA, "at": self.at, "git_sha": self.git_sha, "region": self.region,
            "account": self.account, "design": self.design,
            "site_host": self.site_host, "mode": self.mode, "ok": self.ok,
            "coverage_gaps": self.coverage_gaps(),
            "outcomes": [{**asdict(o), "passed": o.passed} for o in self.outcomes],
            "summary": summary_rows(self), "cleanup": self.cleanup, "notes": self.notes,
        }


def _verdict(outcomes: list[Outcome], wanted: str) -> str:
    relevant = [o for o in outcomes if o.expected == wanted]
    if not relevant:
        return "not run"
    return wanted if all(o.passed for o in relevant) else "FAILED"


def summary_rows(receipt: Receipt) -> list[dict[str, str]]:
    """One row per layer: how the decoy fared, how Jordan fared and who refused the decoy.

    A decoy refusal counts only beside a passing Jordan control at the same layer, because a
    layer that refuses everyone proves nothing about the decoy. Without one the decoy shows
    ``unproven`` and no refuser is named.
    """
    rows = []
    for layer in LAYERS:
        mine = [o for o in receipt.outcomes if o.layer == layer]
        decoy = [o for o in mine if o.actor == DECOY]
        jordan = _verdict([o for o in mine if o.actor == JORDAN], ALLOWED)
        decoy_verdict = _verdict(decoy, REFUSED)
        if decoy_verdict == REFUSED and jordan != ALLOWED:
            decoy_verdict = UNPROVEN
        refusers = sorted({REFUSER_LABELS.get(o.refused_by or "", "") for o in decoy
                           if o.refused_by}) if decoy_verdict == REFUSED else []
        rows.append({
            "layer": layer, "decoy": decoy_verdict, "jordan": jordan,
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


def write_private_text(path: Path, text: str) -> None:
    """Write ``text`` mode 0600 through a temporary file renamed over ``path``.

    The temporary file is opened without following a symlink, and the rename replaces a symlink
    at ``path`` instead of writing through it.
    """
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


def write_receipt(path: Path, receipt: Receipt) -> None:
    """Write the receipt as JSON, mode 0600, through a temporary file renamed over ``path``.

    Raises:
        ValueError: When the serialized receipt would hold a secret or an identifier that
            ``leaks`` finds; nothing is written.
    """
    text = json.dumps(receipt.to_dict(), indent=2) + "\n"
    found = leaks(text)
    if found:
        raise ValueError(f"the receipt would contain {', '.join(found)}; it was not written")
    write_private_text(path, text)
