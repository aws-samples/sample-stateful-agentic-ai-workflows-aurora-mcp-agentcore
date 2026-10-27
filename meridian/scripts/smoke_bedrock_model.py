#!/usr/bin/env python3
"""Confirm which thinking and effort settings Claude Sonnet 5.5 accepts on Amazon Bedrock.

A dry run by default: it prints the candidates and calls nothing. With ``--apply`` and
``--i-understand-this-changes-aws`` it sends one tiny Converse request per candidate (a few
tokens each, billed to the calling account) and prints, for each, whether Bedrock accepted it,
the stop reason, the token usage, the wall-clock latency and whether text came back. It never
sends a ``temperature``, a forced ``toolChoice``, ``thinking: disabled`` or a ``budget_tokens``:
this model rejects them.

The baseline candidate (no optional field) runs first. If Bedrock refuses it, the failure is
request-wide: it is reported INCONCLUSIVE, the other candidates are not sent, and the exit code
is 2. If it is accepted, a later REJECTED stands as specific to that setting.

Each candidate ends in one of four outcomes:
  USABLE        Bedrock accepted the request and the reply carried text.
  ACCEPTED      Bedrock accepted the request but no text came back (for example a max_tokens
                stop while thinking). Not a recommendation: raise MAX_TOKENS and rerun.
  REJECTED      Bedrock refused the request shape (a ValidationException, or a client error
                that names the setting).
  INCONCLUSIVE  The error says nothing about the setting: authorization, throttling, timeouts,
                connection and service errors. Fix the cause and rerun.
MAX_TOKENS is 1024 so adaptive thinking has room to think and still answer a one-line prompt;
thinking tokens count against it. The thinking between_tools form is valid only at effort high
or below; it is sent at the default effort, which is high, so a later change of that default
would make it fail for a reason unrelated to the setting. Latencies are single samples and the
first includes client set-up.

Usage:
    cd meridian
    venv/bin/python scripts/smoke_bedrock_model.py
    venv/bin/python scripts/smoke_bedrock_model.py --apply --i-understand-this-changes-aws

Exit codes: 0 at least one USABLE candidate, 1 every candidate conclusive and none usable,
2 no usable candidate and at least one INCONCLUSIVE, 3 the confirmation flag is missing.
The setting to keep goes in REQUEST_FIELDS in backend/bedrock_model.py and in
meridian_agentcore/app/MeridianConcierge/model/load.py. The effort result is only reported;
nothing here applies it.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.bedrock_model import REQUEST_FIELDS  # noqa: E402
from backend.config import config  # noqa: E402
from backend.security.redaction import mask_account_ids  # noqa: E402
from backend.timing import clock, elapsed_ms  # noqa: E402
from scripts.identity_release.settings import CONFIRM_FLAG  # noqa: E402

PROMPT = "Reply with the single word ready."
MAX_TOKENS = 1024
ADAPTIVE: Dict[str, Any] = {"type": "adaptive"}
CANDIDATES = (
    ("provider default (no thinking field)", {}),
    ("adaptive thinking", {"thinking": ADAPTIVE}),
    ("thinking between_tools", {"thinking": {"type": "between_tools"}}),
    ("adaptive thinking, effort low",
     {"thinking": ADAPTIVE, "output_config": {"effort": "low"}}),
    ("adaptive thinking, effort medium",
     {"thinking": ADAPTIVE, "output_config": {"effort": "medium"}}),
    ("adaptive thinking, effort high",
     {"thinking": ADAPTIVE, "output_config": {"effort": "high"}}),
)


USABLE = "USABLE"
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"
INCONCLUSIVE = "INCONCLUSIVE"
SETTING_WORDS = ("thinking", "effort", "output_config", "additionalmodelrequestfields", "budget")
BASELINE_FAILURE = (
    "the request fails without any optional field: check the model id, Region and access"
)
INCONCLUSIVE_ADVICE = (
    "          not a verdict on the setting: check credentials, model access, quota and the "
    "Region, then rerun"
)


@dataclass(frozen=True)
class Probe:
    """One candidate's result."""

    label: str
    fields: Dict[str, Any]
    outcome: str
    detail: str
    elapsed_ms: Optional[int]
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    stop_reason: Optional[str] = None

    @property
    def is_effort(self) -> bool:
        """Whether this candidate sets an effort level."""
        return "output_config" in self.fields


def converse_request(model_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """The smallest Converse request that exercises one candidate setting."""
    request: Dict[str, Any] = {
        "modelId": model_id,
        "messages": [{"role": "user", "content": [{"text": PROMPT}]}],
        "inferenceConfig": {"maxTokens": MAX_TOKENS},
    }
    if fields:
        request["additionalModelRequestFields"] = fields
    return request


def classify_error(exc: Exception) -> str:
    """REJECTED when Bedrock refused the request shape, INCONCLUSIVE for anything else."""
    if not isinstance(exc, ClientError):
        return INCONCLUSIVE
    error = exc.response.get("Error", {})
    if error.get("Code") == "ValidationException":
        return REJECTED
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    names_setting = any(word in str(error.get("Message", "")).lower() for word in SETTING_WORDS)
    return REJECTED if status == 400 and names_setting else INCONCLUSIVE


def error_detail(exc: Exception) -> str:
    """The error code (or class) and its masked, shortened message."""
    name = exc.response.get("Error", {}).get("Code") if isinstance(exc, ClientError) else None
    message = mask_account_ids(str(exc))[:160]
    return f"{name or exc.__class__.__name__}: {message}"


def run_probe(client: Any, model_id: str, label: str, fields: Dict[str, Any]) -> Probe:
    """Send one request. A refusal is the answer this probe exists to find, not a crash."""
    started = clock()
    try:
        response = client.converse(**converse_request(model_id, fields))
    except Exception as exc:  # noqa: BLE001 - the error text is the evidence
        return Probe(label, fields, classify_error(exc), error_detail(exc), elapsed_ms(started))
    latency = elapsed_ms(started)
    blocks = response.get("output", {}).get("message", {}).get("content", [])
    has_text = bool("".join(block.get("text", "") for block in blocks).strip())
    usage = response.get("usage", {})
    return Probe(
        label, fields, USABLE if has_text else ACCEPTED, "", latency,
        usage.get("inputTokens"), usage.get("outputTokens"), response.get("stopReason"),
    )


def run_all(client: Any, model_id: str) -> List[Probe]:
    """Probe the baseline first: a baseline refusal is request-wide, not about any setting.

    The baseline sends no optional field, so if Bedrock refuses it the model id, Region or
    access is wrong. It becomes INCONCLUSIVE and the other candidates are not sent.
    """
    (base_label, base_fields), *others = CANDIDATES
    baseline = run_probe(client, model_id, base_label, base_fields)
    if baseline.outcome != REJECTED:
        return [baseline, *[run_probe(client, model_id, *candidate) for candidate in others]]
    detail = f"{BASELINE_FAILURE} ({baseline.detail})"
    failed = replace(baseline, outcome=INCONCLUSIVE, detail=detail)
    skipped = [
        Probe(label, fields, INCONCLUSIVE, "not sent: the baseline failed", None)
        for label, fields in others
    ]
    return [failed, *skipped]


def text_note(probe: Probe) -> str:
    """Whether text came back, and when it did not, why."""
    if probe.outcome == USABLE:
        return "text returned"
    if probe.stop_reason == "max_tokens":
        return "accepted; reply did not fit in maxTokens"
    return "accepted; no text returned"


def probe_lines(probe: Probe) -> List[str]:
    """An error is one line with the masked message; a reply adds a line of measurements."""
    if probe.outcome in (REJECTED, INCONCLUSIVE):
        timing = "" if probe.elapsed_ms is None else f", {probe.elapsed_ms} ms"
        head = f"{probe.outcome:<12}  {probe.label}: {probe.detail}{timing}"
        return [head] + ([INCONCLUSIVE_ADVICE] if probe.outcome == INCONCLUSIVE else [])
    tokens = (
        f"input {_count(probe.input_tokens)} tokens, output {_count(probe.output_tokens)} tokens"
    )
    return [
        f"{probe.outcome:<12}  {probe.label}",
        f"              stop {probe.stop_reason or 'n/a'}, {tokens}, "
        f"{probe.elapsed_ms} ms, {text_note(probe)}",
    ]


def _count(tokens: Optional[int]) -> str:
    return "n/a" if tokens is None else str(tokens)


def effort_lines(probes: Sequence[Probe]) -> List[str]:
    """How each effort level fared. Reported only: REQUEST_FIELDS is not touched."""
    efforts = [probe for probe in probes if probe.is_effort]
    parts = []
    for outcome in (USABLE, ACCEPTED, REJECTED, INCONCLUSIVE):
        levels = [p.fields["output_config"]["effort"] for p in efforts if p.outcome == outcome]
        parts.append(f"{outcome.lower()} {', '.join(levels) or 'none'}")
    return [
        f"Effort: {'; '.join(parts)}.",
        "REQUEST_FIELDS is not changed for effort: choose a level, then change both files.",
    ]


def advice_lines(probes: Sequence[Probe]) -> List[str]:
    """The first usable candidate and what the constants must be, or why there is none."""
    usable = next((probe for probe in probes if probe.outcome == USABLE), None)
    if usable is not None:
        return [
            f"First usable: {usable.label}. REQUEST_FIELDS in backend/bedrock_model.py and in "
            f"meridian_agentcore/app/MeridianConcierge/model/load.py must be {usable.fields!r}.",
            "They already are." if REQUEST_FIELDS == usable.fields
            else f"They are {REQUEST_FIELDS!r} now: change both files.",
        ]
    lines = ["No candidate was usable. Do not release. REQUEST_FIELDS is not recommended."]
    if any(probe.outcome == ACCEPTED for probe in probes):
        lines.append(
            "Some candidates were accepted but returned no text: raise MAX_TOKENS and rerun."
        )
    if any(probe.outcome == INCONCLUSIVE for probe in probes):
        lines.append("Some candidates were inconclusive: fix the cause named above and rerun.")
    return lines


def render(model_id: str, probes: Sequence[Probe]) -> List[str]:
    """The table, the first usable candidate, the effort result and what the constants are."""
    lines = [f"Model: {mask_account_ids(model_id)}", f"Region: {config.bedrock.region}", ""]
    for probe in probes:
        lines.extend(probe_lines(probe))
    note = "Latencies are single samples; the first includes client set-up."
    return [*lines, "", note, *advice_lines(probes), *effort_lines(probes)]


def exit_code(probes: Sequence[Probe]) -> int:
    """0 when a candidate was usable, 2 when none was and one was inconclusive, else 1."""
    if any(probe.outcome == USABLE for probe in probes):
        return 0
    return 2 if any(probe.outcome == INCONCLUSIVE for probe in probes) else 1


def build_parser() -> argparse.ArgumentParser:
    """The command line: a dry run unless both flags are given."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--apply", action="store_true", help="call Amazon Bedrock")
    parser.add_argument(CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply: the calls are billed")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print the plan, or with both flags probe each candidate and print the verdicts."""
    args = build_parser().parse_args(argv)
    model_id = config.bedrock.model_id
    if not args.apply:
        shown = mask_account_ids(model_id)
        print(f"Dry run: no Bedrock call. Model {shown} in {config.bedrock.region}.")
        for label, fields in CANDIDATES:
            print(f"  would try: {label} -> {fields!r}")
        return 0
    if not args.confirmed:
        print(f"--apply also needs {CONFIRM_FLAG}; it calls Amazon Bedrock and is billed.",
              file=sys.stderr)
        return 3
    client = boto3.client("bedrock-runtime", region_name=config.bedrock.region)
    probes = run_all(client, model_id)
    for line in render(model_id, probes):
        print(line)
    return exit_code(probes)


if __name__ == "__main__":
    sys.exit(main())
