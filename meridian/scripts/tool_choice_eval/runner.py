"""Build the real Concierge agent over stub tools and score what the model chooses.

The agent is the Runtime's own: the shared prompt (``concierge_definition.system_prompt(4)``)
plus the turn instructions of ``MeridianConcierge/prompts.py``, the same ``TravelerPinHook``,
and tool specs read from the Gateway's schema files. On a confirmed turn the model gets no
tools and the platform's narration prompt, as in ``main.converse``. The model is the Runtime's
(``model/load.py``): ``BEDROCK_MODEL_ID`` or its default.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from scripts import stage_concierge_runtime
from scripts.tool_choice_eval import surface
from scripts.tool_choice_eval.cases import Case
from scripts.tool_choice_eval.scoring import CaseScore, errored, score_case
from scripts.tool_choice_eval.stubs import CallRecorder, StubTool

RUNTIME_DIR = stage_concierge_runtime.RUNTIME_DIR
EVAL_TRAVELER = "trv_eval_traveler"
CONFIRMED_ACTIONS = {"confirmed_hold": "hold", "confirmed_booking": "booking"}


@dataclass(frozen=True)
class Runtime:
    """The Runtime modules the eval reuses, imported from the Concierge's own folder."""

    definition: Any
    prompts: Any
    trace_spans: frozenset[str]


def load_runtime() -> Runtime:
    """Stage and import the Runtime's shared definition, prompts and trace table.

    The Runtime cannot import ``backend``, so its modules are top-level names in its own folder.
    """
    stage_concierge_runtime.stage()
    if str(RUNTIME_DIR) not in sys.path:
        sys.path.insert(0, str(RUNTIME_DIR))
    import concierge_definition  # noqa: PLC0415 - importable only after the path is set
    import prompts  # noqa: PLC0415
    import turn_trace  # noqa: PLC0415

    return Runtime(concierge_definition, prompts, frozenset(turn_trace.SPANS))


def load_runtime_model() -> Any:
    """The Strands model the deployed Concierge uses (``BEDROCK_MODEL_ID`` or its default)."""
    if str(RUNTIME_DIR) not in sys.path:
        sys.path.insert(0, str(RUNTIME_DIR))
    from model.load import load_model  # noqa: PLC0415

    return load_model()


def resolved_model_id() -> str:
    """The model id ``load_model`` would use, read without building a client."""
    if str(RUNTIME_DIR) not in sys.path:
        sys.path.insert(0, str(RUNTIME_DIR))
    from model.load import DEFAULT_MODEL_ID  # noqa: PLC0415

    return os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)


def stub_tools(runtime: Runtime) -> list[StubTool]:
    """One stub per tool the Runtime hands the model, with the Gateway's name and schema."""
    visible = surface.model_visible(surface.gateway_tools(), runtime.trace_spans)
    return [StubTool(name, surface.strands_spec(name, tool)) for name, tool in visible.items()]


def messages_for(case: Case) -> list[dict[str, Any]]:
    """The earlier conversation of a case in Strands' message shape."""
    return [{"role": role, "content": [{"text": text}]} for role, text in case.history]


def turn_prompt_for(case: Case, runtime: Runtime) -> str:
    """The prompt the Runtime sends the model on this case's turn."""
    if case.offers_tools:
        return runtime.prompts.turn_prompt(case.prompt, case.memory_context, None, False)
    return runtime.prompts.narration_prompt(
        case.outcome, case.target, case.budget_ceiling_cents, CONFIRMED_ACTIONS[case.turn])


def build_eval_agent(
    case: Case, model: Any, runtime: Runtime, recorder: CallRecorder
) -> Any:
    """The Concierge agent for one case: its prompt, its hooks, and stub tools or none."""
    tools = stub_tools(runtime) if case.offers_tools else []
    hold, booking = case.turn == "confirmed_hold", case.turn == "confirmed_booking"
    instructions = runtime.prompts.turn_instructions(
        hold, case.target if hold else None, booking, case.target if booking else None)
    return runtime.definition.build_agent(
        model=model,
        tools=tools,
        hooks=[runtime.definition.TravelerPinHook(EVAL_TRAVELER), recorder],
        phase=4,
        extra_instructions=instructions,
        callback_handler=None,
        messages=messages_for(case),
    )


def run_case(case: Case, model: Any, runtime: Runtime) -> CaseScore:
    """Run one case once and score the calls. A model failure is an errored score."""
    recorder = CallRecorder()
    try:
        agent = build_eval_agent(case, model, runtime, recorder)
        agent(turn_prompt_for(case, runtime))
    except Exception as error:  # noqa: BLE001 - reported as an inconclusive run, never swallowed
        return errored(case, f"{type(error).__name__}: {_one_line(error)}")
    return score_case(case, recorder.calls)


def _one_line(error: BaseException) -> str:
    from backend.security.redaction import mask_account_ids  # noqa: PLC0415

    return mask_account_ids(" ".join(str(error).split()))[:300]


def run_cases(
    cases: Iterable[Case],
    model: Any,
    runtime: Runtime,
    *,
    repeats: int = 1,
    progress: Callable[[CaseScore], None] | None = None,
) -> list[CaseScore]:
    """Run every case ``repeats`` times, in order, and return one score per run."""
    scores = []
    for case in cases:
        for _ in range(repeats):
            score = run_case(case, model, runtime)
            scores.append(score)
            if progress:
                progress(score)
    return scores


def fingerprint(runtime: Runtime, cases: Iterable[Case]) -> SimpleNamespace:
    """What a result was measured against: hashes of the prompt, the tool specs and the cases.

    A score is comparable to another only when these match, apart from the model.
    """
    specs = "".join(
        f"{tool.tool_name}|{tool.tool_spec['description']}|{tool.tool_spec['inputSchema']}"
        for tool in stub_tools(runtime))
    prompt = runtime.definition.system_prompt(4) + runtime.prompts.UNCONFIRMED
    case_text = "".join(f"{c.id}|{c.turn}|{c.prompt}|{c.history}" for c in cases)

    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

    return SimpleNamespace(
        prompt=digest(prompt), tools=digest(specs), cases=digest(case_text),
        tool_names=sorted(tool.tool_name for tool in stub_tools(runtime)),
    )
