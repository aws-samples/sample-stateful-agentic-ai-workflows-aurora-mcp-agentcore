"""The tool-choice eval set: one JSON object per line of ``cases.jsonl``, validated on load.

A case is one traveler turn. It names the tools the model must call, the extra tools it may
call, and the arguments that matter. Every write tool is forbidden unless the case expects or
allows it, so a read-only question that ends in a hold or a booking fails the case.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from backend.agents.concierge_definition import WRITE_TOOLS

CASES_PATH = Path(__file__).with_name("cases.jsonl")
TURNS = ("unconfirmed", "confirmed_hold", "confirmed_booking")
MATCHERS = ("equals", "equals_ci", "contains_ci", "set_equals")
ROLES = ("user", "assistant")
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
CASE_KEYS = frozenset({
    "id", "turn", "prompt", "memory_context", "history", "outcome", "target",
    "budget_ceiling_cents", "expected_tools", "allowed_tools", "forbidden_tools",
    "expected_arguments", "note",
})


class CaseError(ValueError):
    """A line of the eval set is not a usable case."""


@dataclass(frozen=True)
class Case:
    """One traveler turn and what the model should do with it.

    Attributes:
        id: Unique slug, used in reports and to select a case.
        turn: ``unconfirmed`` (the model has the tools), or ``confirmed_hold`` and
            ``confirmed_booking`` (the platform already placed the write; the model has none).
        prompt: The traveler's words. Empty on a confirmed turn.
        memory_context: Authorized traveler context the Runtime adds to the prompt.
        history: Earlier messages as ``(role, text)`` pairs.
        outcome: The Gateway's decision a confirmed turn narrates.
        target: The hold or booking terms a confirmed turn narrates.
        budget_ceiling_cents: The saved ceiling a confirmed turn narrates.
        expected_tools: Bare tool names the model must call.
        allowed_tools: Bare tool names it may call besides the expected ones.
        forbidden_tools: Bare tool names it must never call, on top of the unlisted writes.
        expected_arguments: ``{tool: {argument: {matcher: value}}}``.
        note: Why the case exists.
    """

    id: str
    turn: str
    prompt: str
    memory_context: str
    history: tuple[tuple[str, str], ...]
    outcome: str
    target: dict | None
    budget_ceiling_cents: int
    expected_tools: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    expected_arguments: Mapping[str, Mapping[str, Mapping[str, Any]]]
    note: str

    @property
    def offers_tools(self) -> bool:
        """Whether the model has tools on this turn."""
        return self.turn == "unconfirmed"

    def forbidden(self) -> frozenset[str]:
        """Every tool this case forbids: the listed ones plus each write it does not allow."""
        unlisted_writes = set(WRITE_TOOLS) - set(self.expected_tools) - set(self.allowed_tools)
        return frozenset(self.forbidden_tools) | unlisted_writes


def _names(raw: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(n, str) and n for n in value):
        raise CaseError(f"{where}: {key} must be a list of tool names")
    if len(set(value)) != len(value):
        raise CaseError(f"{where}: {key} repeats a tool")
    return tuple(value)


def _history(raw: Mapping[str, Any], where: str) -> tuple[tuple[str, str], ...]:
    entries = raw.get("history", [])
    if not isinstance(entries, list):
        raise CaseError(f"{where}: history must be a list")
    pairs = []
    for entry in entries:
        valid = isinstance(entry, dict) and set(entry) == {"role", "text"}
        if not valid or entry["role"] not in ROLES or not isinstance(entry["text"], str):
            raise CaseError(f"{where}: each history entry is {{role: user|assistant, text}}")
        pairs.append((entry["role"], entry["text"]))
    return tuple(pairs)


def _arguments(raw: Mapping[str, Any], where: str) -> dict:
    value = raw.get("expected_arguments", {})
    if not isinstance(value, dict):
        raise CaseError(f"{where}: expected_arguments must be an object")
    for tool, arguments in value.items():
        if not isinstance(arguments, dict) or not arguments:
            raise CaseError(f"{where}: expected_arguments.{tool} must name arguments")
        for argument, matcher in arguments.items():
            if not isinstance(matcher, dict) or len(matcher) != 1:
                raise CaseError(f"{where}: {tool}.{argument} needs exactly one matcher")
            if next(iter(matcher)) not in MATCHERS:
                raise CaseError(f"{where}: {tool}.{argument} matcher must be {MATCHERS}")
    return value


def _check_consistency(case: Case, where: str) -> None:
    expected, allowed, forbidden = (
        set(case.expected_tools), set(case.allowed_tools), set(case.forbidden_tools))
    if expected & forbidden or allowed & forbidden:
        raise CaseError(f"{where}: a tool is both wanted and forbidden")
    if expected & allowed:
        raise CaseError(f"{where}: a tool is both expected and allowed")
    unknown = set(case.expected_arguments) - expected - allowed
    if unknown:
        raise CaseError(f"{where}: arguments for tools the case does not call: {sorted(unknown)}")
    if case.turn == "unconfirmed":
        if not case.prompt:
            raise CaseError(f"{where}: an unconfirmed turn needs a prompt")
        return
    if case.prompt or not case.outcome or not case.target:
        raise CaseError(f"{where}: a confirmed turn has an outcome and a target, and no prompt")
    if expected or allowed or case.expected_arguments:
        raise CaseError(f"{where}: a confirmed turn gives the model no tools to expect")


def parse_case(raw: Any, where: str = "case") -> Case:
    """Validate one decoded line and return the case.

    Raises:
        CaseError: The line is not an object, has an unknown key, or breaks a rule above.
    """
    if not isinstance(raw, dict):
        raise CaseError(f"{where}: a case is a JSON object")
    unknown = sorted(set(raw) - CASE_KEYS)
    if unknown:
        raise CaseError(f"{where}: unknown keys {unknown}")
    case_id = raw.get("id")
    if not isinstance(case_id, str) or not ID_PATTERN.match(case_id):
        raise CaseError(f"{where}: id must match {ID_PATTERN.pattern}")
    if raw.get("turn", "unconfirmed") not in TURNS:
        raise CaseError(f"{where}: turn must be one of {TURNS}")
    texts = {k: raw.get(k, "") for k in ("prompt", "memory_context", "outcome", "note")}
    if not all(isinstance(v, str) for v in texts.values()):
        raise CaseError(f"{where}: prompt, memory_context, outcome and note are strings")
    target = raw.get("target")
    if target is not None and not isinstance(target, dict):
        raise CaseError(f"{where}: target must be an object")
    ceiling = raw.get("budget_ceiling_cents", 0)
    if not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling < 0:
        raise CaseError(f"{where}: budget_ceiling_cents must be a non-negative integer")
    case = Case(
        id=case_id, turn=raw.get("turn", "unconfirmed"), prompt=texts["prompt"],
        memory_context=texts["memory_context"], history=_history(raw, where),
        outcome=texts["outcome"], target=target, budget_ceiling_cents=ceiling,
        expected_tools=_names(raw, "expected_tools", where),
        allowed_tools=_names(raw, "allowed_tools", where),
        forbidden_tools=_names(raw, "forbidden_tools", where),
        expected_arguments=_arguments(raw, where), note=texts["note"],
    )
    _check_consistency(case, where)
    return case


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    """Read and validate every case in ``path``.

    Raises:
        CaseError: A line is not valid JSON or not a valid case, an id repeats, or the file
            holds no case.
    """
    cases: list[Case] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{path.name} line {number}"
        try:
            raw = json.loads(line)
        except ValueError as error:
            raise CaseError(f"{where}: not valid JSON ({error})") from error
        cases.append(parse_case(raw, where))
    ids = [case.id for case in cases]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise CaseError(f"{path.name}: repeated ids {repeated}")
    if not cases:
        raise CaseError(f"{path.name}: no cases")
    return cases
