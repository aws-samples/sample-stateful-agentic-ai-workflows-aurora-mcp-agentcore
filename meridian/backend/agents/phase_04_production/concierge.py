"""
Production mode: the managed AgentCore Runtime owns the tool loop; Aurora RLS bounds the turn.

Walkthrough, AgentCore on one turn
----------------------------------
  1. Workload identity  : the backend's IAM principal from AWS STS (security span)
  2. Traveler grant      : traveler_identity_bindings authorizes the workload for Jordan
  3. Aurora RLS read     : one short transaction for profile, preferences and recall
  4. AgentCore Runtime   : the agent discovers its tools from AgentCore Gateway over MCP,
                           keeps its conversation in AgentCore Memory, searches Aurora,
                           and places a courtesy hold only when the traveler confirmed it.
                           Every tool call is checked by the gateway's Cedar policies.
  5. Aurora RLS writes   : one short transaction commits the turn's audit row; a second
                           stores the turn in memory. Every turn that calls the Runtime
                           commits its audit row, including one whose call fails; that
                           row names the error. A turn that does not complete is not
                           stored in Aurora conversation memory, and any streamed text
                           stays provisional.

This module is **live**: imported by `chat.py` for `production_search()` and
`production_hold()`.

AWS docs (AgentCore):
  - What is AgentCore?
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html
  - Runtime:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html
  - Gateway (managed MCP):
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html
  - Policy (Cedar):
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html
  - Memory:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html
  - Identity:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html

AWS docs (Aurora):
  - RDS Data API transactions (RLS ``scoped_session``):
    https://docs.aws.amazon.com/rdsdataservice/latest/APIReference/API_BeginTransaction.html
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Coroutine, Dict, Iterable, Iterator, List, Optional, Tuple

from backend.agentcore.cli_config import require_agentcore_platform
from backend.agentcore.identity import get_agentcore_identity
from backend.chat_stream import emit_chat_event
from backend.concierge_voice import VOICE
from backend.agentcore.errors import CallerCredentialError
from backend.agentcore.runtime import (
    RuntimeDecision,
    RuntimeTurnError,
    TurnProgress,
    get_agentcore_runtime,
)
from backend.agents.concierge_definition import PACKAGE_SEARCH_TOOLS
from backend.agents.budget import (
    BUDGET_KEYS,
    budget_ceiling_from_facts,
    budget_ceiling_per_traveler_cents,
)
from backend.agents.phase_04_production.audit_outcome import (
    REFUSED_STATUS,
    TurnAudit,
    audit_of_failed_turn,
    layer_words,
    read_turn,
    turn_error_class,
    turn_error_of,
    write_refusal,
)
from backend.agents.phase_04_production.write_outcome import (
    GOVERNED_WRITES,
    PlacedWriteUnfinished,
    WriteOutcomeUnknown,
)
from backend.agents.phase_04_production.memory_agent import (
    ActivityEntry as MemoryActivity,
    MemoryAgent as TravelerMemorySpecialist,
)
from backend.db.rds_data_client import ScopeTimings, get_rds_data_client
from backend.memory.store import get_memory_store
from backend.search_utils import display_sql
from backend.security.redaction import mask_account_ids, mask_payload
from backend.timing import clock, elapsed_ms
from backend.trace_names import PRODUCTION_CODE

logger = logging.getLogger(__name__)


def response_packages(
    decision: RuntimeDecision, remembered: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """Connect cards to packages discussed in the reply, using only observed results.

    Runtime's IDs can contain the entire search pool. Explicit catalog IDs or
    full names in the reply give its presentation order; unmentioned candidates
    must not displace those trips. A generic reply retains the runtime ranking.
    A follow-up without a search can name the same conversation's saved trips;
    they are rehydrated from the live catalog before display.
    """
    searched = any(
        "tools/call" in str(_read(activity, "title"))
        and any(tool in str(_read(activity, "title")) for tool in PACKAGE_SEARCH_TOOLS)
        for activity in decision.activities
    )
    recall_only = not decision.packages and not searched
    pool = remembered if recall_only else decision.packages
    observed = {str(p["package_id"]): p for p in pool or [] if p.get("package_id")}
    mentioned = []
    for package_id, package in observed.items():
        labels = [package_id, str(package.get("name") or "")]
        positions = [match.start() for label in labels if label
                     if (match := re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)",
                                            decision.message, re.IGNORECASE))]
        if positions:
            mentioned.append((min(positions), package_id))
    if mentioned:
        return [observed[package_id] for _, package_id in sorted(mentioned)]
    if recall_only:
        return []
    ranked = list(dict.fromkeys(decision.recommended_package_ids))
    return [observed[package_id] for package_id in ranked if package_id in observed] or list(observed.values())


PACKAGE_DETAIL_SQL = """
    SELECT package_id, name, operator, price_per_person,
           description, image_url, trip_type, destination,
           region, durations, package_places_left(package_id) AS availability,
           highlights
    FROM trip_packages
    WHERE package_id IN ({placeholders})
"""
PERSIST_SQL = (
    "-- Separate short RLS write transaction:\n"
    "INSERT INTO conversation_messages "
    "(message_id, conversation_id, role, content, embedding)\n"
    "  VALUES (..., ..., 'user', $1, $2::vector);\n"
    "INSERT INTO conversation_messages "
    "(message_id, conversation_id, role, content, embedding)\n"
    "  VALUES (..., ..., 'assistant', $3, $4::vector);\n"
    "INSERT INTO trip_interactions "
    "(interaction_id, traveler_id, conversation_id,\n"
    " query_text, response_summary, packages_shown, embedding)\n"
    "  VALUES (..., $5, ..., $1, $3, $6::jsonb, $2::vector);"
)
READ_UNIT_SQL = (
    "-- Short RLS read transaction (SET LOCAL reverts on commit)\n"
    "SELECT set_config('app.current_traveler_id', %s, true),\n"
    "       set_config('app.agent_type', %s, true);\n"
    "SET LOCAL ROLE meridian_app;"
)
CONSOLE_LOGS = (
    "https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1"
    "#logsV2:log-groups/log-group/$252Faws$252Fbedrock-agentcore$252Fruntimes$252F{runtime_id}-DEFAULT"
)


# The backend's own span for a confirmed hold or booking: what became of the governed write.
WRITE_OUTCOME = {"category": "synthesis", "component": "ProductionAgent"}


def _unwritten_outcome(
    decision: RuntimeDecision, audit: Optional[TurnAudit], tool: str, title: str
) -> Tuple[str, Dict[str, Any]]:
    """The title and telemetry of a governed write that did not happen.

    A Cedar denial keeps ``title`` and the status ``denied``. A refusal by another layer, read
    from the row's entry for the write, says refused and names the layer. Anything else keeps
    ``title`` and the status ``error``.
    """
    if decision.policy_decision == "deny":
        return title, {**WRITE_OUTCOME, "status": "denied"}
    layer = write_refusal(audit, tool)
    if layer:
        return f"{title}: refused by {layer_words(layer)}", {
            **WRITE_OUTCOME, "status": REFUSED_STATUS,
            "fields": [{"label": "refused_by", "value": layer}],
        }
    return title, {**WRITE_OUTCOME, "status": "error"}


def _read(item: Any, key: str) -> Any:
    """One attribute of a span, whether it is a dict or an object."""
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def runtime_model_id(activities: Iterable[Any]) -> Optional[str]:
    """The model id the AgentCore Runtime reported for this turn, if it reported one.

    The Runtime uses its configured ``BedrockModel``, with no fallback chain, and
    names its id in the ``model`` field of its runtime span. That id is the model
    that wrote the reply. The configured model is never substituted.

    Args:
        activities: The turn's spans, as dicts or objects with ``telemetry``.

    Returns:
        The reported model id, or None when no runtime span carries one.
    """
    for activity in activities:
        telemetry = _read(activity, "telemetry")
        if telemetry is None or _read(telemetry, "category") != "runtime":
            continue
        for row in _read(telemetry, "fields") or []:
            if _read(row, "label") == "model" and _read(row, "value"):
                return str(_read(row, "value"))
    return None


@dataclass(frozen=True)
class HoldTarget:
    """The exact terms the traveler confirmed with the Hold button."""

    package_id: str
    duration: str
    travelers: int
    unit_price_cents: int


@dataclass
class HoldOutcome:
    """What the governed hold produced, allow or deny."""

    hold: Optional[Dict[str, Any]]
    refused: Optional[str]
    policy_decision: Optional[str]
    activities: List[Any]
    message: str
    conv_id: str
    trace_id: Optional[str]


def _with_budget_fact(
    facts: List[Dict[str, Any]], budget_facts: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Keep the fact the ceiling comes from in the context the reply is built on.

    The specialist ranks preferences by confidence and truncates, which drops
    Jordan's saved budget. The gateway still enforces a ceiling derived from it, so
    a reply written without it tells the traveler that no budget is on file while
    the policy is holding them to one.

    Args:
        facts: The ranked facts the specialist returned for this turn.
        budget_facts: The full preference set the ceiling is derived from.

    Returns:
        ``facts`` unchanged when it already carries a budget fact, otherwise with
        the first budget fact from the full set appended.
    """
    if {str(fact.get("key", "")).lower() for fact in facts} & set(BUDGET_KEYS):
        return facts
    for fact in budget_facts:
        if str(fact.get("key", "")).lower() in BUDGET_KEYS:
            return [*facts, fact]
    return facts


@dataclass
class BookingTarget:
    """The held booking the traveler confirmed with the Confirm button, as Aurora holds it."""

    booking_id: str
    total_cents: int
    package_id: str
    duration: str
    travelers: int


@dataclass
class BookingOutcome:
    """What the governed confirmation produced, allow or deny."""

    booking: Optional[Dict[str, Any]]
    refused: Optional[str]
    policy_decision: Optional[str]
    activities: List[Any]
    message: str
    conv_id: str
    trace_id: Optional[str]


@dataclass
class AuthorizedRead:
    """What the short RLS read unit produced for one turn."""

    scope: Any
    conv_id: str
    memory_context: str
    memory_facts: List[Dict[str, Any]]
    budget_facts: List[Dict[str, Any]]
    remembered_packages: List[Dict[str, Any]] = field(default_factory=list)
    audit: Optional[TurnAudit] = None
    # The verified Cognito subject of this turn's signed-in caller. It lives on the turn, not
    # the agent, so two requests can never swap subjects.
    verified_subject: Optional[str] = None


@contextmanager
def _naming_a_placed_write(placed_write: Optional[str], step: str) -> Iterator[None]:
    """Raise ``PlacedWriteUnfinished`` for an error in ``step`` once ``placed_write`` happened.

    With ``placed_write`` None the error passes through unchanged. So does an error a nested
    step already named, and a cancellation, which is not an ``Exception``.
    """
    try:
        yield
    except PlacedWriteUnfinished:
        raise
    except Exception as exc:
        if placed_write is None:
            raise
        raise PlacedWriteUnfinished(placed_write, step) from exc


def _unfinished_write(
    error: BaseException, operation: str, progress: TurnProgress
) -> Optional[Exception]:
    """What a failed Runtime call means for the hold or booking ``operation`` places.

    Returns:
        ``PlacedWriteUnfinished`` when the Runtime reported the write placed before the failure,
        including a stream that broke after that report. ``WriteOutcomeUnknown`` when the
        Runtime may have run the turn but reported neither the write nor its refusal. None when
        the operation places no write, the write was refused, the turn never ran, the error is
        the caller's credential, or the error is a cancellation.
    """
    write = GOVERNED_WRITES.get(operation)
    if write is None or not isinstance(error, Exception):
        return None
    reported = progress.decision or getattr(error, "decision", None)
    if reported is not None and getattr(reported, write):
        return PlacedWriteUnfinished(write, "reply")
    if _outcome_unknown(error, reported, write, progress):
        return WriteOutcomeUnknown(write)
    return None


def _outcome_unknown(
    error: Exception, reported: Optional[RuntimeDecision], write: str, progress: TurnProgress
) -> bool:
    """Whether the Runtime may have run the write without reporting it or its refusal."""
    refused = reported is not None and bool(getattr(reported, f"{write}_refused"))
    if refused or isinstance(error, CallerCredentialError):
        return False
    return progress.may_have_run


# What the log says when a turn's audit row is lost; each line ends with the turn's ids.
AUDIT_WRITE_FAILED = (
    "The audit row was not written: its write failed while the turn was cancelled (%s)"
)
AUDIT_WRITE_CANCELLED = (
    "The audit row may not have been written: its write was cancelled (%s)"
)
AUDIT_WRITE_FAILED_AFTER_RUNTIME_ERROR = (
    "The audit row was not written: its write failed after the Runtime call failed (%s)"
)
# What the log says when a row names its error by the class alone.
TURN_ERROR_UNREADABLE = (
    "The audit row names the error by its class alone: its text could not be read (%s)"
)


def _turn_ids(
    read: AuthorizedRead, decision: Optional[RuntimeDecision], traveler_id: str, operation: str
) -> str:
    """The ids that find a turn's audit row, its conversation and its Runtime session.

    A Runtime call that raised with nothing reported (``decision`` None) names neither.
    """
    if decision is None:
        session, trace = "unknown", "unknown"
    else:
        session, trace = decision.runtime_session_id, decision.trace_id or "pending"
    return (
        f"operation={operation} traveler={traveler_id} conversation={read.conv_id} "
        f"runtime_session={session} trace={trace}"
    )


def _turn_error(error: BaseException, turn: str) -> str:
    """The row's ``turn_error`` for ``error``; its class alone when its text cannot be read.

    Reading an error's text runs its own ``__str__``. When that raises, the row still gets
    written, naming the class (``turn_error_class``), and a warning with the turn's ids keeps
    what the text raised.

    Args:
        error: The exception that ended the turn.
        turn: The turn's ids, for the log line.

    Returns:
        ``turn_error_of(error)``, or ``turn_error_class(error)`` when that raises.
    """
    try:
        return turn_error_of(error)
    except Exception:
        logger.warning(TURN_ERROR_UNREADABLE, turn, exc_info=True)
        return turn_error_class(error)


def _log_unfinished_audit(task: asyncio.Task, turn: str) -> None:
    """Log an audit write that a cancellation of its turn did not see succeed."""
    if task.cancelled():
        logger.error(AUDIT_WRITE_CANCELLED, turn)
    elif task.exception() is not None:
        logger.error(AUDIT_WRITE_FAILED, turn, exc_info=task.exception())


async def _write_through_cancellation(
    write: Coroutine[Any, Any, None], turn: str, in_flight: Optional[BaseException]
) -> None:
    """Run a turn's audit write to its end; a cancellation of the turn always wins over its error.

    By the time a turn writes, the Runtime has acted, so the turn's audit row must be written.
    The write runs as a task of its own under ``asyncio.shield``: a cancellation of the turn
    waits for it, however often it arrives, and then proceeds. A write that fails while a
    cancellation is in flight, whether the cancellation arrived during the write or before it
    (``in_flight``), is logged at error level and the cancellation continues. A write whose own
    task is cancelled, as process teardown does, is logged as possibly lost.

    Args:
        write: The audit unit's coroutine, not yet started.
        turn: The turn's ids, for the log line.
        in_flight: The exception unwinding the caller's ``try``, or None.
    """
    task = asyncio.create_task(write)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.wait({task})
            except asyncio.CancelledError:
                continue
        _log_unfinished_audit(task, turn)
        raise
    except Exception:
        if not isinstance(in_flight, asyncio.CancelledError):
            raise
        logger.error(AUDIT_WRITE_FAILED, turn, exc_info=True)


class ProductionAgent:
    """
    Phase 4 concierge orchestrator.

    Authorizes the workload for the traveler, reads memory under RLS, hands the
    authorized context to the managed AgentCore Runtime (which owns the gateway
    tool loop), then persists the returned decision under RLS.
    """

    AGENT_FILE = "agents/phase_04_production/concierge.py"
    RUNTIME_FILE = "meridian_agentcore/app/MeridianConcierge/main.py"

    def __init__(self, activity_callback: Optional[Callable[[MemoryActivity], Any]] = None):
        self.activity_callback = activity_callback or (lambda _: None)
        self.traveler_memory = TravelerMemorySpecialist(activity_callback=self.activity_callback)
        self.store = get_memory_store()
        self.db = get_rds_data_client()
        self.identity = get_agentcore_identity()
        self.agentcore_runtime = get_agentcore_runtime()

    def _log(self, activity_type: str, title: str, details: Optional[str] = None, **kwargs) -> None:
        self.activity_callback(
            MemoryActivity(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                activity_type=activity_type,
                title=title,
                details=mask_account_ids(details) if details else details,
                sql_query=kwargs.get("sql_query"),
                execution_time_ms=kwargs.get("execution_time_ms"),
                agent_name=kwargs.get("agent_name", PRODUCTION_CODE),
                agent_file=kwargs.get("agent_file", self.AGENT_FILE),
                telemetry=mask_payload(kwargs.get("telemetry")),
            )
        )

    def _forward(self, span: Dict[str, Any]) -> None:
        """Replay a span the runtime emitted as a backend activity."""
        self.activity_callback(
            MemoryActivity(
                id=str(span.get("id") or uuid.uuid4()),
                timestamp=str(span.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                activity_type=str(span.get("activity_type") or "reasoning"),
                title=str(span.get("title") or "Runtime span"),
                details=mask_payload(span.get("details")),
                sql_query=span.get("sql_query"),
                execution_time_ms=span.get("execution_time_ms"),
                agent_name=str(span.get("agent_name") or PRODUCTION_CODE),
                agent_file=str(span.get("agent_file") or self.RUNTIME_FILE),
                telemetry=mask_payload(span.get("telemetry")),
            )
        )

    def _collect(self, activities: List[Any]) -> None:
        """Route both the memory specialist's spans and this concierge's into one list."""
        outer_callback = self.activity_callback

        def collect(entry: MemoryActivity) -> None:
            activities.append(entry)
            outer_callback(entry)

        self.activity_callback = collect
        self.traveler_memory.activity_callback = collect

    # ------------------------------------------------------------- identity

    def _identity_spans(self, scope: Any, traveler_id: str, timings: ScopeTimings) -> None:
        self._log(
            "reasoning",
            "Workload identity: AWS STS",
            details="The backend acts as its IAM principal, from sts:GetCallerIdentity",
            telemetry={
                "category": "security",
                "component": "AWS STS",
                "status": "delegated",
                "fields": [
                    {"label": "iam_identity", "value": scope.iam_identity, "mono": True},
                ],
            },
        )
        self._log(
            "security",
            "Workload traveler grant allowed",
            details=(
                f"{scope.authorization.provider}:{scope.authorization.subject_id} -> {traveler_id}"
            ),
            execution_time_ms=timings.grant_ms,
            telemetry={
                "category": "security",
                "component": "Aurora identity binding",
                "status": "ok",
                "fields": [
                    {"label": "authorization.provider", "value": scope.authorization.provider},
                    {
                        "label": "authorization.subject",
                        "value": scope.authorization.subject_id,
                        "mono": True,
                    },
                    {"label": "authorization.decision", "value": "allow"},
                    {"label": "traveler_id", "value": traveler_id, "mono": True},
                    {"label": "binding_table", "value": "traveler_identity_bindings", "mono": True},
                ],
            },
        )
        self._log(
            "security",
            "Aurora RLS: short read unit",
            details=(
                f"app.current_traveler_id={traveler_id}, role=meridian_app, "
                "commits before external calls"
            ),
            sql_query=display_sql(READ_UNIT_SQL, (traveler_id, "concierge_agent")),
            execution_time_ms=timings.rls_ms,
            telemetry={
                "category": "security",
                "component": "Aurora RLS",
                "status": "ok",
                "fields": [
                    {"label": "iam_identity", "value": scope.iam_identity, "mono": True},
                    {"label": "rls.traveler_id", "value": traveler_id, "mono": True},
                    {"label": "rls.role", "value": "meridian_app", "mono": True},
                    {"label": "transaction.unit", "value": "read"},
                ],
            },
        )

    # ------------------------------------------------------------ read unit

    async def _authorized_read(
        self, message: str, traveler_id: str, conversation_id: Optional[str],
        *, verified_subject: Optional[str] = None,
    ) -> AuthorizedRead:
        """Authorize the workload and read the traveler's memory in one short RLS unit.

        ``verified_subject`` is the signed-in caller's verified Cognito subject, carried on the
        returned read so the turn's audit row names this caller.
        """
        scope = await asyncio.to_thread(self.identity.scope_for_turn)
        query_vector = await asyncio.to_thread(
            self.store.prepare_embedding_vector, message, input_type="search_query"
        )
        self.traveler_memory._prepared_query_vector = query_vector
        self.traveler_memory._query_vector_prepared = True
        timings = ScopeTimings()
        try:
            async with self.db.scoped_session(
                traveler_id=traveler_id,
                agent_type="concierge_agent",
                authorization=scope.authorization,
                timings=timings,
            ) as read_tx:
                self.traveler_memory._transaction_id = read_tx
                self._identity_spans(scope, traveler_id, timings)
                conv_id = await self.store.get_or_create_conversation(
                    traveler_id, conversation_id, transaction_id=read_tx
                )
                profile = await self.store.recall_profile(traveler_id, transaction_id=read_tx)
                session = await self.traveler_memory.recall_session_context(conv_id)
                remembered_packages = await self.store.recall_shown_packages(conv_id, transaction_id=read_tx)
                prefs = await self.traveler_memory.recall_traveler_preferences(traveler_id)
                similar = await self.traveler_memory.recall_similar_interactions(
                    traveler_id, message
                )
                # The specialist recalls the top facts by confidence for the reply; the
                # budget ceiling the policy compares against needs every fact.
                budget_facts = await self.store.recall_preferences(
                    traveler_id, limit=50, transaction_id=read_tx
                )
        finally:
            self.traveler_memory._transaction_id = None
            self.traveler_memory._prepared_query_vector = None
            self.traveler_memory._query_vector_prepared = False
        facts = _with_budget_fact(prefs.get("facts", []), budget_facts)
        context = self.store.format_memory_context(
            profile, session.get("turns", []), facts, similar.get("interactions", [])
        )
        if remembered_packages:
            context += "\nPreviously shown trips (recheck availability before a hold):\n" + "\n".join(
                f"- {package['package_id']}: {package.get('name', '')}" for package in remembered_packages
            )
        return AuthorizedRead(
            scope=scope,
            conv_id=conv_id,
            memory_context=context,
            memory_facts=facts,
            budget_facts=budget_facts,
            remembered_packages=remembered_packages,
            verified_subject=verified_subject,
        )

    # -------------------------------------------------------------- runtime

    def _runtime_span(self, decision: RuntimeDecision) -> None:
        runtime_id = decision.runtime_arn.rsplit("/", 1)[-1]
        # The Runtime measures its own turn and reports it; no report, no number.
        self._log(
            "reasoning",
            "AgentCore Runtime: turn complete",
            details=(
                f"session={decision.runtime_session_id}, "
                f"trace={decision.trace_id or 'pending'}"
            ),
            execution_time_ms=decision.elapsed_ms,
            agent_file=self.RUNTIME_FILE,
            telemetry={
                "category": "runtime",
                "component": "Bedrock AgentCore Runtime, MeridianConcierge",
                "status": "ok",
                "fields": [
                    {"label": "runtime_arn", "value": decision.runtime_arn, "mono": True},
                    {"label": "runtimeSessionId", "value": decision.runtime_session_id, "mono": True},
                    {"label": "trace_id", "value": decision.trace_id or "pending", "mono": True},
                    {"label": "trace_console", "value": CONSOLE_LOGS.format(runtime_id=runtime_id)},
                    {"label": "qualifier", "value": decision.qualifier},
                    {"label": "isolation", "value": decision.isolation},
                    {
                        "label": "tokens",
                        "value": (
                            f"in {decision.usage.get('inputTokens', '-')}, "
                            f"out {decision.usage.get('outputTokens', '-')}"
                        ),
                    },
                    {"label": "decision_source", "value": "managed Runtime"},
                ],
            },
        )

    async def _runtime_turn(
        self, read: AuthorizedRead, message: str, traveler_id: str, travelers: int, *,
        operation: str, **hold,
    ) -> Tuple[RuntimeDecision, List[Dict[str, Any]]]:
        """Call the managed Runtime and record the turn's audit facts on ``read``.

        Every turn that calls the Runtime has one audit row written for it. When the call
        returns a decision, the caller writes the row once it has handled the decision. When
        the call raises, whether from a Runtime error event, a transport error, a timeout or a
        cancellation, this method writes the row, naming the error, through
        ``_audit_failed_turn`` and then re-raises the original error. Nothing is stored in
        conversation memory for that turn. A write that fails is logged with the turn's ids
        and the row is lost; the original error still propagates. A cancellation that arrives
        during the write propagates instead of the original error once the write has ended, as
        on every path.

        Args:
            read: The turn's read unit; the audit facts are recorded on it.
            message: The traveler's message for this turn.
            traveler_id: Traveler identifier (RLS scope).
            travelers: Party size, which sizes the budget ceiling the policy sees.
            operation: The operation the audit row records, such as ``production_turn``.
            **hold: The confirmed hold or booking terms the Runtime receives.

        Returns:
            The Runtime's decision and its spans as the trace shows them, not yet shown.

        Raises:
            PlacedWriteUnfinished: The Runtime reported the hold or booking this operation
                places, then the call failed; the error is the cause.
            WriteOutcomeUnknown: For a hold or booking, the Runtime may have run the turn but
                reported neither the write nor its refusal; the error is the cause.
            BaseException: Otherwise whatever the Runtime call raised, unchanged, once its audit
                write has ended, or a cancellation that arrived during that write.
        """
        ceiling = budget_ceiling_from_facts(read.budget_facts, travelers)
        per_traveler = budget_ceiling_per_traveler_cents(read.budget_facts)
        saved_budget = (
            f"Saved budget cap: ${per_traveler / 100:,.2f} per traveler."
            if per_traveler is not None
            else "No saved per-traveler budget is available; the platform default applies."
        )
        # Put the current, calculated basis before recalled prose so it survives
        # context truncation and cannot be confused with an earlier party size.
        budget_context = (
            f"Current turn pricing basis:\nParty size: {travelers} traveler(s).\n"
            f"{saved_budget}\nWhole-party policy ceiling: ${ceiling / 100:,.2f}.\n"
            "Catalog prices are per traveler for the entire listed package duration, "
            "not nightly rates. Total = price_per_person × party size only; never multiply "
            "by nights. No extra tax or fee is modeled. Compare that total with the "
            "whole-party ceiling; claim it is within budget only when total <= ceiling. "
            "Offer only durations and availability returned by get_package_details. "
            "For recommendations, quote the catalog price per traveler only. Leave party "
            "totals and budget arithmetic to the trip controls, which calculate them exactly. "
            "Do not quote calculated totals or budget comparisons in recommendation prose."
        )
        progress = TurnProgress()
        try:
            decision = await asyncio.to_thread(
                self.agentcore_runtime.invoke_turn,
                read.conv_id,
                traveler_id,
                message,
                f"{VOICE}\n\n{budget_context}\n\n{read.memory_context}",
                budget_ceiling_cents=ceiling,
                travelers_count=travelers,
                progress=progress,
                **hold,
            )
        except BaseException as error:
            await self._audit_failed_turn(read, traveler_id, operation, error)
            unfinished = _unfinished_write(error, operation, progress)
            if unfinished is not None:
                raise unfinished from error
            raise
        turn = read_turn(decision)
        read.audit = turn.audit
        return decision, turn.spans

    def _forward_turn(self, decision: RuntimeDecision, spans: List[Dict[str, Any]]) -> None:
        """Forward the Runtime's spans to the trace, then the backend's span for its turn."""
        for span in spans:
            self._forward(span)
        self._runtime_span(decision)

    async def _hydrate(self, packages_raw: List[Dict[str, Any]]) -> List[Any]:
        """Join the reply's packages to live catalog rows, preserving presentation order."""
        package_ids = [str(p.get("package_id") or "") for p in packages_raw if p.get("package_id")]
        if package_ids:
            placeholders = ", ".join(["%s"] * len(package_ids))
            started = clock()
            detail_rows = await self.db.execute(
                PACKAGE_DETAIL_SQL.format(placeholders=placeholders), tuple(package_ids)
            )
            query_ms = elapsed_ms(started)
            detail_by_id = {str(row.get("package_id") or ""): dict(row) for row in detail_rows}
            packages_raw = [
                {
                    **package,
                    **detail_by_id.get(str(package.get("package_id") or ""), {}),
                    **({"similarity": package["similarity"]} if "similarity" in package else {}),
                }
                for package in packages_raw if str(package.get("package_id") or "") in detail_by_id
            ]
            self._log(
                "database",
                "Refreshed discussed trips from the live catalog",
                details=f"{len(detail_rows)} live catalog rows joined to the response's trip order",
                sql_query=display_sql(
                    PACKAGE_DETAIL_SQL.format(placeholders=placeholders), tuple(package_ids)
                ),
                execution_time_ms=query_ms,
                telemetry={
                    "category": "data",
                    "component": "Aurora trip_packages",
                    "status": "ok",
                    "fields": [
                        {"label": "rows", "value": str(len(detail_rows))},
                        {"label": "preserved_order", "value": "Trips discussed in the response; Runtime ranking when none are named"},
                    ],
                },
            )
        return [
            SimpleNamespace(
                package_id=p.get("package_id", ""),
                name=p.get("name", ""),
                operator=p.get("operator", ""),
                price_per_person=float(p.get("price_per_person", 0.0) or 0.0),
                description=p.get("description", "") or "",
                image_url=p.get("image_url", "") or "",
                trip_type=p.get("trip_type", "") or "",
                destination=p.get("destination"),
                region=p.get("region"),
                durations=p.get("durations") or [],
                availability=p.get("availability") or {},
                highlights=p.get("highlights") or [],
                similarity=p.get("similarity"),
            )
            for p in packages_raw
        ]

    # ------------------------------------------------------------ write unit

    def _audit_identity(self, read: AuthorizedRead) -> Tuple[str, str]:
        """The provider and subject an audit row names: the signed-in person when known."""
        if read.verified_subject:
            return "cognito", read.verified_subject
        return read.scope.authorization.provider, read.scope.authorization.subject_id

    async def _audit_unit(
        self, read: AuthorizedRead, traveler_id: str, operation: str, rows_returned: int
    ) -> None:
        """Write and commit the turn's audit row in its own short RLS write transaction.

        It commits before the turn's memory is written and apart from it, so no memory
        failure can roll the row back.
        """
        audit = read.audit
        if audit is None:
            raise RuntimeError("the turn's audit facts were not recorded before the audit unit")
        provider, subject = self._audit_identity(read)
        async with self.db.scoped_session(
            traveler_id=traveler_id,
            agent_type="concierge_agent",
            authorization=read.scope.authorization,
        ) as audit_tx:
            await self.store.write_audit(
                agent_name="ProductionAgent",
                operation=operation,
                traveler_id=traveler_id,
                rls_traveler=traveler_id,
                rls_agent_type="concierge_agent",
                iam_identity=read.scope.iam_identity,
                authorization_provider=provider,
                authorization_subject=subject,
                authorization_decision=audit.decision,
                rows_returned=rows_returned,
                transaction_id=audit_tx,
                tool_calls=audit.tool_calls,
                turn_error=audit.turn_error,
            )

    async def _audit_turn(
        self, read: AuthorizedRead, decision: RuntimeDecision, traveler_id: str, operation: str,
        rows_returned: int, *, in_flight: Optional[BaseException],
    ) -> None:
        """Write the turn's audit row from the ``finally`` that follows the Runtime's return.

        A turn that failed after the Runtime returned and before this row, such as in the
        trace forwarding or the catalog refresh, has its error (``in_flight``) named in
        ``turn_error``, so the row is ``deny`` or ``error``, never ``allow``. ``turn_error``
        stays NULL only for a turn that completed, or one that failed after its row was
        committed (the memory step and the trace entries that follow it). An error whose text
        cannot be read is named by its class alone (``_turn_error``), so the row is always
        written once the Runtime has returned, and ``in_flight`` still propagates.

        Args:
            read: The turn's read unit, with its audit facts.
            decision: The Runtime's decision, whose ids a lost write's log line names.
            traveler_id: Traveler identifier (RLS scope).
            operation: The operation the row records, such as ``production_turn``.
            rows_returned: How many trips the turn showed.
            in_flight: The exception that ``finally`` is handling (captured explicitly).
        """
        turn = _turn_ids(read, decision, traveler_id, operation)
        if in_flight is not None:
            read.audit = replace(read.audit, turn_error=_turn_error(in_flight, turn))
        await _write_through_cancellation(
            self._audit_unit(read, traveler_id, operation, rows_returned), turn, in_flight)

    async def _audit_failed_turn(
        self, read: AuthorizedRead, traveler_id: str, operation: str, error: BaseException
    ) -> None:
        """Write the audit row of a turn whose Runtime call raised ``error``.

        The row names ``error`` in ``turn_error`` and records the calls the Runtime reported
        before its error event, or unknown calls when it reported nothing. Its decision is
        ``deny`` or ``error``, and it shows no trips. It commits in its own short transaction
        under the same cancellation shield as every other turn's row. An error whose text cannot
        be read is named by its class alone (``_turn_error``). A row that cannot be written is
        logged with the turn's ids and never replaces ``error``, which the caller re-raises; a
        cancellation that arrives during the write still wins, as on every path.

        Args:
            read: The turn's read unit; the audit facts are recorded on it.
            traveler_id: Traveler identifier (RLS scope).
            operation: The operation the row records, such as ``production_hold``.
            error: The exception the Runtime call raised (captured explicitly).
        """
        reported = error.decision if isinstance(error, RuntimeTurnError) else None
        turn = _turn_ids(read, reported, traveler_id, operation)
        try:
            read.audit = audit_of_failed_turn(reported, _turn_error(error, turn))
            await _write_through_cancellation(
                self._audit_unit(read, traveler_id, operation, 0), turn, error
            )
        except Exception:
            logger.error(AUDIT_WRITE_FAILED_AFTER_RUNTIME_ERROR, turn, exc_info=True)

    async def _memory_unit(
        self, read: AuthorizedRead, traveler_id: str, message: str, reply: str,
        shown: List[Dict[str, Any]], *, placed_write: Optional[str] = None,
    ) -> None:
        """Store the turn in conversation memory in its own short RLS write transaction.

        The turn's audit row is already committed. The write's trace entries reach the activity
        callback only after this transaction commits, so a failing callback cannot roll the
        memory back. ``placed_write`` names the hold or booking this turn placed, if any: a
        failure to store the turn then raises ``PlacedWriteUnfinished`` for the memory step. A
        failing trace callback after the commit does not, because the turn was stored.
        """
        with _naming_a_placed_write(placed_write, "memory"):
            held = await self._store_turn(read, traveler_id, message, reply, shown)
        memory_callback = self.traveler_memory.activity_callback
        for entry in held:
            memory_callback(entry)
        self._log(
            "tool_call",
            "Strands @tool persist_turn",
            details=(
                "Reauthorized traveler scope; wrote 2 messages + 1 trip_interaction "
                f"in a short RLS write unit, {len(shown)} packages recorded"
            ),
            sql_query=PERSIST_SQL,
            telemetry={
                "category": "memory_short",
                "component": "Aurora write path: scoped_session",
                "status": "ok",
                "fields": [
                    {"label": "table", "value": "conversation_messages + trip_interactions"},
                    {"label": "rls.traveler_id", "value": traveler_id, "mono": True},
                    {"label": "transaction.unit", "value": "write"},
                ],
            },
        )

    async def _store_turn(
        self, read: AuthorizedRead, traveler_id: str, message: str, reply: str,
        shown: List[Dict[str, Any]],
    ) -> List[MemoryActivity]:
        """Embed the turn, then write it in one RLS write transaction.

        Returns:
            The memory specialist's trace entries, held back until the transaction committed.
        """
        interaction_text = f"User: {message}\nAssistant: {reply}"
        user_vector, assistant_vector, interaction_vector = await asyncio.gather(
            asyncio.to_thread(
                self.store.prepare_embedding_vector, message, input_type="search_document"
            ),
            asyncio.to_thread(
                self.store.prepare_embedding_vector, reply, input_type="search_document"
            ),
            asyncio.to_thread(
                self.store.prepare_embedding_vector, interaction_text, input_type="search_document"
            ),
        )
        self.traveler_memory._prepared_turn_vectors = {
            "user": user_vector,
            "assistant": assistant_vector,
            "interaction": interaction_vector,
        }
        memory_callback = self.traveler_memory.activity_callback
        held: List[MemoryActivity] = []
        self.traveler_memory.activity_callback = held.append
        try:
            async with self.db.scoped_session(
                traveler_id=traveler_id,
                agent_type="concierge_agent",
                authorization=read.scope.authorization,
            ) as memory_tx:
                self.traveler_memory._transaction_id = memory_tx
                await self.traveler_memory.persist_turn(
                    traveler_id, read.conv_id, message, reply, shown
                )
        finally:
            self.traveler_memory._transaction_id = None
            self.traveler_memory._prepared_turn_vectors = None
            self.traveler_memory.activity_callback = memory_callback
        return held

    async def _settle_governed_write(
        self, read: AuthorizedRead, decision: RuntimeDecision, spans: List[Dict[str, Any]], *,
        traveler_id: str, operation: str, message: str, shown: List[Dict[str, Any]],
        placed_write: Optional[str],
    ) -> None:
        """Show a hold or booking turn's trace, then write its audit row and remember it.

        Once the Runtime returned, the audit row is written whatever happens. When the trace
        fails, only a placed write's turn is remembered: its write happened. A refused one did
        not complete, so it is not stored. After a placed write, a failed audit row or memory
        write raises ``PlacedWriteUnfinished`` for that step; the caller names a trace failure,
        which reaches it unchanged.
        """
        in_flight: Optional[BaseException] = None
        try:
            self._forward_turn(decision, spans)
        except BaseException as exc:
            in_flight = exc
            raise
        finally:
            with _naming_a_placed_write(placed_write, "audit"):
                await self._audit_turn(read, decision, traveler_id, operation, len(shown),
                                       in_flight=in_flight)
            if in_flight is None or placed_write:
                await self._memory_unit(read, traveler_id, message, decision.message, shown,
                                        placed_write=placed_write)

    # ----------------------------------------------------------------- turns

    async def process_turn(
        self,
        message: str,
        traveler_id: str,
        conversation_id: Optional[str],
        limit: int,
        travelers_count: int = 1,
        *,
        authorization_subject: Optional[str] = None,
    ) -> Tuple[List[Any], List[Any], str, str, List[Dict[str, Any]]]:
        """Run one production concierge turn through the full AgentCore envelope.

        Args:
            message: The traveler's utterance for this turn.
            traveler_id: Traveler identifier (RLS scope, e.g. trv_meridian_demo).
            conversation_id: Existing conversation id, or None to start one.
            limit: Kept for the chat router's signature; the runtime ranks its own results.
            travelers_count: Party size, which sizes the budget ceiling the policy sees.
            authorization_subject: The verified Cognito subject of the signed-in caller, or None.

        Returns:
            Tuple of (packages, activities, response_text, conversation_id, memory_facts).
        """
        require_agentcore_platform(require_memory=False)
        activities: List[Any] = []
        self._collect(activities)
        emit_chat_event({"type": "status", "text": "Recalling your travel preferences…"})
        read = await self._authorized_read(
            message, traveler_id, conversation_id, verified_subject=authorization_subject
        )
        emit_chat_event({"type": "conversation", "conversation_id": read.conv_id})
        decision, spans = await self._runtime_turn(
            read, message, traveler_id, travelers_count, operation="production_turn")
        shown: List[Dict[str, Any]] = []
        in_flight: Optional[BaseException] = None
        # The Runtime has acted, so the turn's audit row is written whatever happens next.
        try:
            self._forward_turn(decision, spans)
            emit_chat_event(
                {"type": "status", "text": "Saving your plan and checking trip details…"})
            packages = await self._hydrate(response_packages(decision, read.remembered_packages))
            shown = [{"package_id": p.package_id, "name": p.name} for p in packages]
        except BaseException as exc:
            in_flight = exc
            raise
        finally:
            await self._audit_turn(read, decision, traveler_id, "production_turn", len(shown),
                                   in_flight=in_flight)
        # A turn that did not complete above is not stored in Aurora conversation memory.
        await self._memory_unit(read, traveler_id, message, decision.message, shown)
        self._log(
            "result",
            "Memory-grounded reply ready",
            details=f"{len(packages)} packages, Aurora memory updated",
            telemetry={"category": "synthesis", "component": "ProductionAgent", "status": "ok"},
        )
        return packages, activities, decision.message, read.conv_id, read.memory_facts

    async def process_hold(
        self, traveler_id: str, conversation_id: Optional[str], target: HoldTarget,
        *, authorization_subject: Optional[str] = None,
    ) -> HoldOutcome:
        """Place the hold the traveler confirmed, through the runtime and the governed gateway.

        Args:
            traveler_id: Traveler identifier (RLS scope).
            conversation_id: The conversation the hold belongs to, or None to start one.
            target: The exact hold terms the traveler confirmed with the Hold button.
            authorization_subject: The verified Cognito subject of the signed-in caller, or None.

        Returns:
            The hold outcome. ``hold`` is None when Cedar denied the call or the
            inventory refused it; ``refused`` then carries the reason.
        """
        require_agentcore_platform(require_memory=False)
        activities: List[Any] = []
        self._collect(activities)
        message = (
            f"Place a 12-hour courtesy hold on {target.package_id} ({target.duration}) "
            f"for {target.travelers} traveler(s)."
        )
        read = await self._authorized_read(
            message, traveler_id, conversation_id, verified_subject=authorization_subject
        )
        decision, spans = await self._runtime_turn(
            read, message, traveler_id, target.travelers, operation="production_hold",
            hold_confirmed=True, hold_target=asdict(target),
        )
        shown = [{"package_id": target.package_id, "name": target.package_id}]
        placed = "hold" if decision.hold else None
        # A trace entry that fails from here on, after a placed hold, names the trace step.
        with _naming_a_placed_write(placed, "trace"):
            await self._settle_governed_write(
                read, decision, spans, traveler_id=traveler_id, operation="production_hold",
                message=message, shown=shown, placed_write=placed,
            )
            held = decision.hold or {}
            if decision.hold:
                title, telemetry = "Courtesy hold persisted", {**WRITE_OUTCOME, "status": "held"}
            else:
                title, telemetry = _unwritten_outcome(
                    decision, read.audit, "create_courtesy_hold", "Courtesy hold not placed")
            self._log(
                "result",
                title,
                details=(
                    f"Hold #{held.get('bookingId')}, expires {held.get('expiresAt')}"
                    if decision.hold
                    else (decision.hold_refused or "The runtime did not call the hold tool.")
                ),
                telemetry=telemetry,
            )
        return HoldOutcome(
            hold=decision.hold,
            refused=decision.hold_refused,
            policy_decision=decision.policy_decision,
            activities=activities,
            message=decision.message,
            conv_id=read.conv_id,
            trace_id=decision.trace_id,
        )

    async def process_booking(
        self, traveler_id: str, conversation_id: Optional[str], target: BookingTarget,
        *, authorization_subject: Optional[str] = None,
    ) -> BookingOutcome:
        """Confirm the held booking the traveler approved, through the runtime and the gateway.

        Args:
            traveler_id: Traveler identifier (RLS scope).
            conversation_id: The conversation the booking belongs to, or None to start one.
            target: The held booking as Aurora holds it, read under RLS by the caller.
            authorization_subject: The verified Cognito subject of the signed-in caller, or None.

        Returns:
            The booking outcome. ``booking`` is None when Cedar denied the call or Aurora
            refused it; ``refused`` then carries the reason.
        """
        require_agentcore_platform(require_memory=False)
        activities: List[Any] = []
        self._collect(activities)
        message = (
            f"Confirm booking {target.booking_id}: {target.package_id} ({target.duration}) "
            f"for {target.travelers} traveler(s), total ${target.total_cents / 100:,.2f}."
        )
        read = await self._authorized_read(
            message, traveler_id, conversation_id, verified_subject=authorization_subject
        )
        decision, spans = await self._runtime_turn(
            read, message, traveler_id, target.travelers, operation="production_booking",
            booking_confirmed=True, booking_target=asdict(target),
        )
        shown = [{"package_id": target.package_id, "name": target.package_id}]
        placed = "booking" if decision.booking else None
        # A trace entry that fails from here on, after a placed booking, names the trace step.
        with _naming_a_placed_write(placed, "trace"):
            await self._settle_governed_write(
                read, decision, spans, traveler_id=traveler_id, operation="production_booking",
                message=message, shown=shown, placed_write=placed,
            )
            booked = decision.booking or {}
            if decision.booking:
                title = "Booking confirmed in Aurora"
                telemetry = {**WRITE_OUTCOME, "status": "confirmed"}
            else:
                title, telemetry = _unwritten_outcome(
                    decision, read.audit, "confirm_booking", "Booking not confirmed")
            self._log(
                "result",
                title,
                details=(
                    f"Booking #{booked.get('bookingId')}, confirmed {booked.get('confirmedAt')}"
                    if decision.booking
                    else (decision.booking_refused or "The runtime did not call the confirm tool.")
                ),
                telemetry=telemetry,
            )
        return BookingOutcome(
            booking=decision.booking,
            refused=decision.booking_refused,
            policy_decision=decision.policy_decision,
            activities=activities,
            message=decision.message,
            conv_id=read.conv_id,
            trace_id=decision.trace_id,
        )


def create_production_agent(activity_callback=None) -> ProductionAgent:
    return ProductionAgent(activity_callback=activity_callback)


# Back-compat aliases for older imports and docs.
create_concierge_agent = create_production_agent
ConciergeAgent = ProductionAgent
