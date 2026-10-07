"""The Phase 5 workflow's seven steps, ported from the LangGraph nodes.

Each step takes the folded workflow state and returns the keys it changes,
including the span list with its own spans appended, exactly as the LangGraph
nodes did. ``graph.Step`` turns that output into the delta Strands persists.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.agents.budget import budget_ceiling_from_facts
from backend.agents.phase_05_workflow.governed_hold import (
    HOLD_TOOL,
    LEASE_LOST_ERRORS,
    HoldOutcomeUnknown,
    hold_arguments,
    place_governed_hold,
)
from backend.agents.phase_05_workflow.hold_intent import prepare_hold_node
from backend.agents.phase_05_workflow.packages import (
    first_available_duration,
    package_to_dict,
    top_ranked_package,
)
from backend.agents.phase_05_workflow.routing import classify_intent, is_recovery_request
from backend.agents.phase_05_workflow.state import (
    HOLD_MINUTES,
    SNAPSHOT_STORE,
    activity,
    coerce_activity,
    hold_key,
    utc_now,
)
from backend.db.journey_store import ExecutionLeaseLostError, ScopedDb, ensure_journey

logger = logging.getLogger(__name__)

StepFn = Callable[..., Awaitable[Any]]
GatewayCall = Callable[[str, Dict[str, Any]], Dict[str, Any]]
BOOKING_STATUS_SQL = "SELECT status FROM bookings WHERE booking_id = %s"


class WorkflowNodes:
    """The graph's steps and the hold's helpers.

    Args:
        search_fn: Hybrid trip search, ``(query, limit=5) -> (packages, spans)``.
        availability_fn: Duration availability, ``(query, package_id=None) -> (packages, spans, message)``.
        memory_recall_fn: Saved-context recall for the memory branch, or None to skip it.
        gateway_call: The Gateway ``tools/call`` transport. Defaults to the configured gateway.
    """

    def __init__(
        self,
        search_fn: StepFn,
        availability_fn: StepFn,
        memory_recall_fn: Optional[StepFn] = None,
        *,
        gateway_call: Optional[GatewayCall] = None,
    ) -> None:
        self.search_fn = search_fn
        self.availability_fn = availability_fn
        self.memory_recall_fn = memory_recall_fn
        self._gateway_call = gateway_call or self._configured_gateway

    @staticmethod
    def _configured_gateway(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        from backend.agentcore.gateway import get_agentcore_gateway

        return get_agentcore_gateway().call_tool(name, arguments)

    @staticmethod
    def _snapshot_activity(node: str) -> Dict[str, Any]:
        """Announce the snapshot Strands appends after this node.

        The write runs after the node returns, so this span cannot time it; the
        runner attaches the measured write time. The title keeps the prefix the
        showcase parses until A3 changes the span contract.
        """
        return activity(
            "tool_call",
            "Checkpoint · AuroraSnapshotStorage.write",
            details=f"Workflow snapshot appended after the {node} node",
            sql_query=(
                "INSERT INTO workflow_snapshots\n"
                "  (storage_key, session_id, traveler_id,\n"
                "   execution_id, worker_id, snapshot)\n"
                "VALUES ($1, $2, $3, $4, $5, $6::jsonb);"
            ),
            telemetry={
                "category": "memory_short",
                "component": "Aurora workflow_snapshots",
                "status": "ok",
                "fields": [
                    {"label": "checkpointer", "value": "Strands SnapshotSessionManager"},
                    {"label": "checkpoint_durable", "value": "true"},
                    {"label": "checkpoint_store", "value": "workflow_snapshots"},
                    {"label": "durability", "value": SNAPSHOT_STORE},
                ],
            },
        )

    async def classify(
        self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Entry node: classify the query into an intent that drives routing.

        Writes `intent` into state; the conditional edge out of this node reads
        it to fan out to search / availability / memory_recall (or the 'plan'
        path, which enters at search and chains into availability).
        """
        start = utc_now()
        intent = classify_intent(state["query"])
        elapsed = int((utc_now() - start).total_seconds() * 1000)
        activities = list(state.get("activities", []))
        activities.append(
            activity(
                "reasoning",
                f"Workflow node: classify → {intent}",
                details=f"intent={intent}",
                execution_time_ms=elapsed,
                telemetry={
                    "category": "orchestration",
                    "component": "Strands Graph",
                    "status": "ok",
                    "fields": [
                        {"label": "node", "value": "classify"},
                        {"label": "intent", "value": intent},
                        {"label": "recovery", "value": str(is_recovery_request(state["query"])).lower()},
                        {"label": "checkpointer", "value": SNAPSHOT_STORE},
                    ],
                },
            )
        )
        return {"intent": intent, "activities": activities}

    async def search(
        self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Worker node: run trip discovery (delegates to the Phase 3 search fn).

        Checkpoints state to Aurora after returning. On the 'plan' path this is
        step 1 of 2 — the conditional edge then routes to the availability node.
        """
        start = utc_now()
        raw_packages, search_activities = await self.search_fn(
            state["query"],
            limit=5,
        )
        packages = [package_to_dict(package) for package in raw_packages]
        elapsed = int((utc_now() - start).total_seconds() * 1000)
        activities = list(state.get("activities", []))
        activities.append(
            activity(
                "delegation",
                "Workflow node: search",
                details=f"{len(packages)} packages",
                execution_time_ms=elapsed,
                telemetry={
                    "category": "orchestration",
                    "component": "Strands Graph → SearchAgent",
                    "status": "ok",
                    "fields": [
                        {"label": "node", "value": "search"},
                        {"label": "packages", "value": str(len(packages))},
                    ],
                },
            )
        )
        for sa in search_activities:
            activities.append(coerce_activity(sa))
        activities.append(self._snapshot_activity("search"))
        return {"packages": packages, "activities": activities}

    async def availability(
        self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Worker node: check duration inventory (delegates to Package fn).

        On the 'plan' path this runs AFTER search (step 2 of 2) and layers
        availability onto the prior trip results; for a standalone 'availability'
        intent it surfaces the availability rows directly. Checkpoints after return.
        """
        start = utc_now()
        is_plan = state.get("intent") == "plan"
        prior = [
            package_to_dict(package)
            for package in (state.get("packages", []) or [])
        ]

        if is_plan and prior:
            targets = [
                package
                for package in prior[:3]
                if package.get("product_id") or package.get("package_id")
            ]

            async def check_target(
                package: Dict[str, Any],
            ) -> tuple[List[Any], List[Any], str]:
                package_id = str(
                    package.get("product_id")
                    or package.get("package_id")
                    or ""
                )
                return await self.availability_fn(
                    state["query"],
                    package_id=package_id,
                )

            target_results = (
                await asyncio.gather(
                    *(check_target(package) for package in targets)
                )
                if targets
                else []
            )
            availability_by_id: Dict[str, Dict[str, Any]] = {}
            sub_activities: List[Any] = []
            for avail_packages, target_activities, _msg in target_results:
                sub_activities.extend(target_activities)
                for package in avail_packages:
                    normalized = package_to_dict(package)
                    package_id = str(
                        normalized.get("product_id")
                        or normalized.get("package_id")
                        or ""
                    )
                    if package_id:
                        availability_by_id[package_id] = normalized

            packages = self._merge_availability(prior, availability_by_id)
            availability_checks = len(target_results)
            availability_rows = len(availability_by_id)
        else:
            raw_available, sub_activities, _msg = await self.availability_fn(
                state["query"]
            )
            packages = [
                package_to_dict(package) for package in raw_available
            ]
            availability_checks = 1 if packages else 0
            availability_rows = len(packages)

        elapsed = int((utc_now() - start).total_seconds() * 1000)
        activities = list(state.get("activities", []))
        activities.append(
            self._availability_span(
                fan_out=is_plan and bool(prior),
                is_plan=is_plan,
                checks=availability_checks,
                rows=availability_rows,
                elapsed=elapsed,
            )
        )
        for sa in sub_activities:
            activities.append(coerce_activity(sa))
        activities.append(self._snapshot_activity("availability"))
        return {
            "packages": packages,
            "activities": activities,
            "availability_checks": availability_checks,
        }

    @staticmethod
    def _merge_availability(
        prior: List[Dict[str, Any]], availability_by_id: Dict[str, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        packages: List[Dict[str, Any]] = []
        for package in prior:
            package_id = str(
                package.get("product_id")
                or package.get("package_id")
                or ""
            )
            availability = availability_by_id.get(package_id)
            packages.append(
                {
                    **package,
                    **(
                        {
                            "available_sizes": availability.get(
                                "available_sizes"
                            ),
                            "availability": availability.get(
                                "availability"
                            ),
                        }
                        if availability
                        else {}
                    ),
                }
            )
        return packages

    @staticmethod
    def _availability_span(
        *, fan_out: bool, is_plan: bool, checks: int, rows: int, elapsed: int
    ) -> Dict[str, Any]:
        return activity(
            "delegation",
            (
                "Workflow node: availability fan-out"
                if fan_out
                else "Workflow node: availability"
            ),
            details=(
                f"Checked duration inventory for {checks} "
                "top-ranked trips in parallel"
                if fan_out
                else f"{rows} availability rows"
            ),
            execution_time_ms=elapsed,
            telemetry={
                "category": "orchestration",
                "component": (
                    "Strands Graph → PackageAgent fan-out"
                    if fan_out
                    else "Strands Graph → PackageAgent"
                ),
                "status": "ok",
                "fields": [
                    {"label": "node", "value": "availability"},
                    {
                        "label": "checks",
                        "value": str(checks),
                    },
                    {"label": "rows", "value": str(rows)},
                    {"label": "step", "value": "2 of 2" if is_plan else "1 of 1"},
                ],
            },
        )

    async def prepare_hold(
        self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        prepared = prepare_hold_node(state)
        activities = list(state.get("activities", []))
        activities.append(activity(
            "reasoning", "Workflow node: prepare_hold",
            details="Stable hold intent prepared; the graph saves it before the hold node."
                if prepared.get("hold_intent") else "No eligible package; no hold intent prepared.",
            telemetry={
                "category": "orchestration", "component": "Strands Graph", "status": "ok",
                "fields": [{"label": "node", "value": "prepare_hold"}],
            },
        ))
        return {**prepared, "activities": activities}

    async def hold(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Worker node: place a courtesy hold on the top-ranked option through the gateway.

        This is what makes the durability claim concrete. The previous nodes
        checkpoint *workflow position*; this one commits a row with real
        consequences: inventory is decremented and the hold carries a TTL. A
        worker can die between here and ``synthesize`` and the hold is still
        there on resume, with time remaining, because it lives in Aurora rather
        than in the process.

        The write goes through AgentCore Gateway, so the Cedar policy that
        governs the Phase 4 agent decides this hold too, and the ``MeridianHolds``
        Lambda runs ``create_courtesy_hold`` under its own traveler grant. The
        worker verifies its lease here and passes its execution id so the Lambda
        verifies it again inside the write transaction; the checkpointed request
        and booking ids make a replacement worker replay the same booking with
        the original expiry.
        """
        start = utc_now()
        activities = list(state.get("activities", []))
        packages = state.get("packages", []) or []
        traveler_id = state.get("traveler_id") or ""

        target = top_ranked_package(packages)
        if not target or not traveler_id:
            activities.append(
                activity(
                    "reasoning",
                    "Workflow node: hold skipped",
                    details="No ranked option to hold for this traveler.",
                )
            )
            activities.append(self._snapshot_activity("hold"))
            return {"activities": activities}

        terms = self._hold_terms(state, target)
        hold_id = terms["booking_id"]
        package_id = terms["package_id"]
        quantity = terms["quantity"]
        thread_id = str(state.get("conversation_id") or "")
        configurable = (config or {}).get("configurable", {})
        execution_id = configurable.get("execution_id")
        traveler_confirmed = bool(configurable.get("traveler_confirmed"))

        journey_id, ceiling = await self._prepare_governed_hold(
            traveler_id, thread_id, execution_id, quantity, state
        )
        arguments = hold_arguments(
            terms,
            traveler_id=traveler_id,
            journey_ref=thread_id,
            budget_ceiling_cents=ceiling,
            hold_minutes=HOLD_MINUTES,
            execution_id=execution_id,
            traveler_confirmed=traveler_confirmed,
        )
        outcome = await self._place_hold(arguments)

        if not outcome.placed:
            reason, denied = self._refusal_reason(outcome)
            logger.warning("courtesy hold not placed: %s", outcome.raw_error or reason)
            activities.append(self._hold_not_placed(reason, denied=denied, raw=outcome.raw_error))
            activities.append(self._snapshot_activity("hold"))
            return {"activities": activities}

        hold = outcome.hold or {}
        hold_id = str(hold.get("bookingId") or hold_id)
        journey_id = str(hold.get("journeyId") or journey_id)
        # A replay returns the existing booking without re-counting inventory,
        # so its seat columns are null by design.
        remaining = hold.get("seatsRemaining")
        expires_at = str(hold.get("expiresAt"))
        created_at = str(hold.get("createdAt"))
        observed_at = str(hold.get("observedAt"))
        hold_status = str(hold.get("status"))
        elapsed = int((utc_now() - start).total_seconds() * 1000)
        activities.append(
            self._hold_span(
                outcome=outcome,
                terms=terms,
                hold_id=hold_id,
                journey_id=journey_id,
                elapsed=elapsed,
            )
        )
        activities.append(self._snapshot_activity("hold"))
        return {
            "activities": activities,
            "journey_id": journey_id,
            "hold_id": hold_id,
            "hold_expires_at": expires_at,
            "hold_created_at": created_at,
            "hold_observed_at": observed_at,
            "hold_status": hold_status,
            "hold_package": package_id,
            # Part of the idempotency key, so compensation can recompute it.
            "hold_duration": terms["duration"],
            "hold_seats_remaining": remaining,
        }

    @staticmethod
    def _hold_span(
        *,
        outcome: Any,
        terms: Dict[str, Any],
        hold_id: str,
        journey_id: str,
        elapsed: int,
    ) -> Dict[str, Any]:
        """Build the span for a placed or replayed hold from the Gateway receipt."""
        hold = outcome.hold or {}
        package_id = terms["package_id"]
        duration = terms["duration"]
        quantity = terms["quantity"]
        hold_request_id = terms["hold_request_id"]
        replayed = bool(hold.get("replayed"))
        remaining = hold.get("seatsRemaining")
        expires_at = str(hold.get("expiresAt"))
        created_at = str(hold.get("createdAt"))
        observed_at = str(hold.get("observedAt"))
        hold_status = str(hold.get("status"))
        governance = outcome.governance or {}
        return activity(
            "database",
            "Workflow node: hold",
            details=(
                (
                    f"Replayed the hold already placed for this request on "
                    f"{package_id} ({duration})"
                )
                if replayed
                else (
                    f"Held {quantity} x {duration} on {package_id} until "
                    f"{expires_at} · {remaining} package spots left"
                )
            ),
            sql_query=(
                "-- AgentCore Gateway tools/call " + HOLD_TOOL + "\n"
                "-- Cedar: meridian_hold_governance (ENFORCE) decides on the arguments\n"
                "-- MeridianHolds Lambda: traveler grant, RLS scope, SET LOCAL ROLE meridian_app,\n"
                "--   lease check for this execution, then\n"
                "SELECT booking_id, status, replayed,\n"
                "       seats_available, seats_reserved, seats_remaining\n"
                "FROM create_courtesy_hold($1 .. $11);\n"
                "-- request identity claimed first, then advisory lock and\n"
                "-- capacity check, inside the RLS scope"
            ),
            execution_time_ms=elapsed,
            telemetry={
                "category": "gateway",
                "component": "AgentCore Gateway · MeridianHolds Lambda · Aurora",
                "status": "ok",
                "fields": [
                    {"label": "gateway_tool", "value": HOLD_TOOL, "mono": True},
                    {"label": "cedar_decision", "value": "allow"},
                    {"label": "cedar_policy", "value": "meridian_hold_governance", "mono": True},
                    {"label": "policy_mode", "value": "ENFORCE"},
                    {"label": "workload", "value": str(governance.get("subject") or ""), "mono": True},
                    {"label": "traveler_grant", "value": str(governance.get("decision") or "")},
                    {"label": "hold_id", "value": hold_id, "mono": True},
                    {
                        "label": "hold_request_id",
                        "value": hold_request_id,
                        "mono": True,
                    },
                    {"label": "journey_id", "value": journey_id, "mono": True},
                    {"label": "replayed", "value": "yes" if replayed else "no"},
                    {"label": "package", "value": package_id},
                    {"label": "duration", "value": duration},
                    {"label": "seats_held", "value": str(quantity)},
                    {"label": "seats_remaining", "value": str(remaining) if remaining is not None else ""},
                    {"label": "expires_at", "value": expires_at},
                    {"label": "hold_created_at", "value": created_at},
                    {"label": "hold_observed_at", "value": observed_at},
                    {"label": "hold_status", "value": hold_status},
                ],
            },
        )

    @staticmethod
    def _refusal_reason(outcome: Any) -> tuple[str, bool]:
        """Why the Gateway refused the hold, and whether Cedar policy denied it.

        Raises:
            HoldOutcomeUnknown: The Gateway returned neither a receipt nor a refusal.
        """
        if outcome.policy_decision is None or (
            outcome.policy_decision == "allow" and not outcome.error
        ):
            raise HoldOutcomeUnknown(
                "The hold outcome is unknown. The Gateway returned no reliable "
                "receipt or refusal; resume the same saved hold request."
            )
        denied = outcome.policy_decision == "deny"
        if denied:
            reason = "Cedar policy refused the hold"
        elif outcome.error == "insufficient_inventory":
            reason = "inventory changed"
        else:
            reason = outcome.error or "the gateway returned no hold"
        return reason, denied

    @staticmethod
    def _hold_terms(state: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
        # Terms come from the checkpointed intent, so the hold that runs is the
        # hold that was fingerprinted. Falling back to deriving them again
        # keeps a direct call to this node working.
        intent = state.get("hold_intent") or prepare_hold_node(state).get(
            "hold_intent", {}
        )
        package_id = str(
            intent.get("package_id")
            or target.get("product_id")
            or target.get("package_id")
        )
        duration = str(intent.get("duration") or first_available_duration(target))
        quantity = int(intent.get("quantity") or state.get("travelers_count") or 1)
        unit_price = float(intent.get("unit_price") or target.get("price") or 0)
        # New business intents own a booking ID. Older checkpoints retain the
        # original thread/package key so their replay still finds the same row.
        hold_id = str(intent.get("booking_id") or hold_key(
            state.get("conversation_id") or "", package_id, duration
        ))
        hold_request_id = str(intent.get("hold_request_id") or hold_id)
        return {
            "package_id": package_id,
            "duration": duration,
            "quantity": quantity,
            "unit_price": unit_price,
            "hold_request_id": hold_request_id,
            "booking_id": hold_id,
        }

    async def _place_hold(self, arguments: Dict[str, Any]) -> Any:
        """Call the Gateway; raise unless the outcome is a placement or a clear refusal."""
        try:
            outcome = await asyncio.to_thread(place_governed_hold, self._gateway_call, arguments)
        except Exception as exc:  # noqa: BLE001 - a lost reply cannot prove no write
            logger.warning("courtesy hold outcome unknown: %s", exc)
            # Failing the node leaves prepare_hold's durable intent pending.
            # Resume reuses that identity and reads/replays the original write.
            raise HoldOutcomeUnknown(
                "The hold outcome is unknown. Re-read the saved journey and resume "
                "the same hold request."
            ) from exc

        if outcome.error in LEASE_LOST_ERRORS:
            # The Lambda proved this worker no longer owns the run. A
            # replacement worker owns the hold; checkpointing past it here
            # would make that worker skip the hold.
            raise ExecutionLeaseLostError(f"Hold refused: {outcome.error}")
        return outcome

    async def _prepare_governed_hold(
        self,
        traveler_id: str,
        thread_id: str,
        execution_id: Optional[str],
        quantity: int,
        state: Dict[str, Any],
    ) -> tuple[str, int]:
        """Verify the worker lease, bind the journey, and size the budget ceiling.

        Two short RLS units, both committed before the gateway is called: the
        booking-agent unit checks the lease and binds the thread to its journey;
        the concierge unit reads every saved preference so the ceiling the Cedar
        policy compares against comes from Jordan's own facts.
        """
        from backend.agentcore.identity import get_agentcore_identity
        from backend.db.rds_data_client import get_rds_data_client
        from backend.memory.store import get_memory_store

        db = get_rds_data_client()
        authorization = get_agentcore_identity().authorization_context()
        async with db.scoped_session(
            traveler_id=traveler_id, agent_type="booking_agent", authorization=authorization
        ) as transaction_id:
            scoped = ScopedDb(db, transaction_id)
            if execution_id:
                live = await scoped.execute(
                    "SELECT execution_id FROM journey_executions WHERE execution_id = %s "
                    "AND status = 'running' AND lease_expires_at > CURRENT_TIMESTAMP FOR UPDATE",
                    (execution_id,),
                )
                if not live:
                    raise ExecutionLeaseLostError("Execution lease expired before the hold")
            journey_id = state.get("journey_id") or await ensure_journey(
                scoped, traveler_id, thread_id, SNAPSHOT_STORE
            )
        async with db.scoped_session(
            traveler_id=traveler_id, agent_type="concierge_agent", authorization=authorization
        ) as transaction_id:
            facts = await get_memory_store().recall_preferences(
                traveler_id, limit=50, transaction_id=transaction_id
            )
        return str(journey_id), budget_ceiling_from_facts(facts, quantity)
    @staticmethod
    def _hold_not_placed(reason: str, *, denied: bool, raw: Optional[str]) -> Dict[str, Any]:
        return activity(
            "security" if denied else "error",
            "Workflow node: hold not placed",
            details=f"No package inventory was held ({reason}). The plan continues unheld.",
            telemetry={
                "category": "security" if denied else "tool",
                "component": "Bedrock AgentCore Policy" if denied else "Bedrock AgentCore Gateway",
                "status": "denied" if denied else "error",
                "fields": [
                    {"label": "gateway_tool", "value": HOLD_TOOL, "mono": True},
                    {"label": "gateway_error", "value": raw or "", "mono": True},
                ] + ([
                    {"label": "cedar_decision", "value": "deny"},
                    {"label": "cedar_policy", "value": "meridian_hold_governance", "mono": True},
                    {"label": "policy_mode", "value": "ENFORCE"},
                ] if denied else []),
            },
        )
    async def release_hold(
        self, state: Dict[str, Any], *, expected_hold_id: Optional[str] = None
    ) -> bool:
        """Compensating action: give the seats back. True when a held booking was released.

        A durable workflow that commits inventory needs an answer for "step 3
        failed after step 2 committed". Releasing marks the booking so the
        capacity count in ``create_courtesy_hold`` stops counting it.

        ``expected_hold_id`` scopes the compensation to the run that is
        failing. The persisted state is the *latest* state for the thread, so
        without this a failed run could read a hold from an earlier successful
        run and release it - undoing work that never failed.
        """
        hold_id = state.get("hold_id")
        traveler_id = state.get("traveler_id")
        if not hold_id or not traveler_id:
            return False
        if expected_hold_id is not None and hold_id != expected_hold_id:
            logger.warning(
                "not releasing hold %s: it belongs to a different run than the one that failed",
                hold_id,
            )
            return False
        try:
            from backend.agentcore.identity import get_agentcore_identity
            from backend.db.rds_data_client import get_rds_data_client

            db = get_rds_data_client()
            async with db.scoped_session(
                traveler_id=traveler_id,
                agent_type="booking_agent",
                authorization=get_agentcore_identity().authorization_context(),
            ) as transaction_id:
                rows = await db.execute(
                    "UPDATE bookings SET status = 'released' "
                    "WHERE booking_id = %s AND traveler_id = %s AND status = 'held' "
                    "RETURNING booking_id",
                    (hold_id, traveler_id),
                    transaction_id=transaction_id,
                )
            logger.info("released courtesy hold %s", hold_id)
            return bool(rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not release hold %s: %s", hold_id, exc)
            return False
    async def memory_recall(
        self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Worker node: recall prior context (delegates to the Phase 4 memory fn).

        Skips gracefully if no memory function is wired. Checkpoints after return,
        matching the search and availability nodes.
        """
        start = utc_now()
        activities = list(state.get("activities", []))
        if self.memory_recall_fn is None:
            activities.append(
                activity(
                    "reasoning",
                    "Workflow node: memory_recall (skipped)",
                    details="No memory recall function wired",
                )
            )
            return {"packages": [], "activities": activities}
        packages, sub_activities = await self.memory_recall_fn(
            state["query"],
            traveler_id=state.get("traveler_id", ""),
            conversation_id=state.get("conversation_id", ""),
        )
        elapsed = int((utc_now() - start).total_seconds() * 1000)
        activities.append(
            activity(
                "delegation",
                "Workflow node: memory_recall",
                details=f"{len(packages)} memory hits",
                execution_time_ms=elapsed,
                telemetry={
                    "category": "orchestration",
                    "component": "Strands Graph → ProductionAgent",
                    "status": "ok",
                    "fields": [{"label": "node", "value": "memory_recall"}],
                },
            )
        )
        for sa in sub_activities:
            activities.append(coerce_activity(sa))
        activities.append(self._snapshot_activity("memory_recall"))
        return {"packages": [package_to_dict(p) for p in packages], "activities": activities}

    async def synthesize(
        self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Terminal node: compose the user-facing reply from accumulated state.

        All branches converge here before END. The response wording reflects the
        intent — notably the 'plan' path narrates the two-step, checkpointed run.
        """
        packages = state.get("packages", []) or []
        intent = state.get("intent", "search")
        availability_checks = state.get("availability_checks", 0)
        hold_status = state.get("hold_status")
        if state.get("hold_id"):
            # The booking row is the record of truth: compensation may have
            # released it after the hold step saved "held".
            hold_status = await self._booking_status(
                str(state.get("traveler_id") or ""), str(state["hold_id"])
            ) or hold_status
        checkpoint_clause = "each step saved to Aurora so the plan can pause and resume"
        if intent == "plan":
            response = (
                ("Recovery plan: searched the catalog, then checked "
                 if is_recovery_request(state.get("query", ""))
                 else "Planned the extension: searched the catalog, then checked ") +
                f"live duration options for the top {availability_checks} "
                f"choices — {checkpoint_clause}."
                if packages
                else "Ran the full plan graph (search → availability), but no "
                "trips matched — try broadening the destination."
            )
        elif intent == "availability" and packages:
            response = (
                f"Found duration inventory for {len(packages)} matching trips."
            )
        elif intent == "memory_recall":
            response = (
                f"Recalled session + preference context, then matched {len(packages)} trips."
                if packages
                else "Recalled prior context — no new catalog matches for that query."
            )
        elif packages:
            response = f"Workflow returned {len(packages)} trips that match your request."
        else:
            response = "No matches yet — try broadening the destination or dates."

        # Action status is application data. A prose model must not turn an
        # existing hold into an offer to place another one, or imply a flight
        # was reserved just because the traveler prefers a nonstop route.
        if state.get("hold_id") and hold_status == "released":
            response += (
                f"\n\nCourtesy hold {state['hold_id']} was released after a step failed, "
                "so those seats are no longer held. Place a new hold to reserve them again."
            )
        elif state.get("hold_id"):
            response += (
                f"\n\nAurora recorded courtesy hold {state['hold_id']} for "
                f"{state.get('hold_package')}, {state.get('hold_duration')}. "
                f"Recorded status: {hold_status or 'held'}. "
                f"Expires at {state.get('hold_expires_at')}. "
                "Read the current receipt before confirming the trip."
            )
        elif is_recovery_request(state.get("query", "")):
            response += "\n\nNo courtesy hold was recorded by this workflow. Review the hold decision before continuing."
        response += "\n\nAvailability refers to trip-package inventory. Flight seats and routes have not been checked or reserved."

        activities = list(state.get("activities", []))
        activities.append(
            activity(
                "result",
                "Workflow node: synthesize",
                details=response,
                telemetry={
                    "category": "synthesis",
                    "component": "Strands Graph",
                    "status": "ok",
                    "fields": [
                        {"label": "intent", "value": intent},
                        {"label": "packages", "value": str(len(packages))},
                        {
                            "label": "availability_checks",
                            "value": str(availability_checks),
                        },
                    ],
                },
            )
        )
        return {"response": response, "activities": activities}

    # ------------------------------------------------------------------- run

    async def _booking_status(self, traveler_id: str, booking_id: str) -> Optional[str]:
        """The booking's current status, read under the traveler's RLS scope."""
        from backend.agentcore.identity import get_agentcore_identity
        from backend.db.rds_data_client import get_rds_data_client

        db = get_rds_data_client()
        async with db.scoped_session(
            traveler_id=traveler_id, agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            rows = await db.execute(BOOKING_STATUS_SQL, (booking_id,), transaction_id=tx)
        return str(rows[0]["status"]) if rows else None
