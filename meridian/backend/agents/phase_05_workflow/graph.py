"""The Phase 5 workflow as a Strands Graph whose state lives in Aurora.

Each node is a deterministic step. Its result message carries a JSON delta:
the state keys it changed and the spans it emitted. Strands persists every
node result in the graph snapshot, so folding the snapshot rebuilds the
workflow state in any process, with nothing kept in memory between runs.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from strands.agent.agent_result import AgentResult
from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import BeforeNodeCallEvent
from strands.multiagent import GraphBuilder
from strands.multiagent.base import NodeResult, Status
from strands.multiagent.graph import Graph, GraphState
from strands.storage import Storage
from strands.telemetry.metrics import EventLoopMetrics

from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from backend.agents.phase_05_workflow.routing import is_recovery_request, pauses_after_search

logger = logging.getLogger(__name__)

GRAPH_ID = "phase5"
NODE_IDS = (
    "classify", "search", "availability", "memory_recall", "prepare_hold", "hold", "synthesize",
)
REVIEW_INTERRUPT = "review_shortlist"
CONFIRM_INTERRUPT = "confirm_hold"
LAST_NODE = "synthesize"
# Generous for an acyclic graph: a replayed step counts as another execution.
MAX_NODE_EXECUTIONS = 3 * len(NODE_IDS)


def pause_point(node: Optional[str]) -> Optional[str]:
    """Return ``node`` if a run can pause after it.

    Raises:
        ValueError: If ``node`` names no node that another node follows.
    """
    pausable = [node_id for node_id in NODE_IDS if node_id != LAST_NODE]
    if node is not None and node not in pausable:
        raise ValueError(
            f"pause_after={node!r} would never pause the run: name one of {', '.join(pausable)}"
        )
    return node


@dataclass
class RunContext:
    """What one invocation knows that the snapshot does not.

    Attributes:
        thread_id: The workflow thread, also the Strands session id.
        execution_id: The execution holding the lease.
        traveler_confirmed: The traveler resumed after reviewing the plan.
        review_requested: The traveler asked to review the shortlist first
            (the request's ``review_only``).
        pause_after: Stop after this node, before the next one starts; used by
            the scripted proofs. Any node except ``synthesize``, which nothing
            follows.

    Raises:
        ValueError: If ``pause_after`` names no node that another node follows.
    """

    thread_id: str
    execution_id: Optional[str]
    traveler_confirmed: bool
    review_requested: bool
    pause_after: Optional[str] = None

    def __post_init__(self) -> None:
        pause_point(self.pause_after)

    def configurable(self) -> Dict[str, Any]:
        """The values a step's ``config["configurable"]`` carries."""
        return {
            "thread_id": self.thread_id,
            "execution_id": self.execution_id,
            "traveler_confirmed": self.traveler_confirmed,
        }


def run_task(**inputs: Any) -> str:
    """The graph task: the run's inputs as JSON, persisted in every snapshot."""
    return json.dumps(inputs, sort_keys=True)


def snapshot_key(thread_id: str) -> str:
    """Where SnapshotSessionManager keeps the graph's latest snapshot.

    The layout is documented in the Strands session-management guide.
    """
    return f"session/{thread_id}/scopes/multiAgent/{GRAPH_ID}/snapshots/snapshot_latest.json"


def _delta_text(result: Any) -> Optional[str]:
    if isinstance(result, NodeResult):
        if result.status != Status.COMPLETED or not isinstance(result.result, AgentResult):
            return None
        message = result.result.message
    elif isinstance(result, dict):
        if result.get("status") != Status.COMPLETED.value:
            return None
        inner = result.get("result") or {}
        message = inner.get("message") if inner.get("type") == "agent_result" else None
    else:
        return None
    content = (message or {}).get("content") or []
    return content[0].get("text") if content else None


def _deltas(
    task: Any, order: List[str], results: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[Tuple[str, Dict[str, Any]]]]:
    inputs = json.loads(task) if isinstance(task, str) else {}
    deltas = []
    for node_id in order:
        text = _delta_text(results.get(node_id))
        if text is not None:
            deltas.append((node_id, json.loads(text)))
    return inputs, deltas


def _fold(task: Any, order: List[str], results: Dict[str, Any]) -> Dict[str, Any]:
    inputs, deltas = _deltas(task, order, results)
    folded: Dict[str, Any] = {**inputs, "activities": []}
    for _node_id, delta in deltas:
        folded["activities"] = folded["activities"] + list(delta.get("spans", []))
        folded.update(delta.get("state", {}))
    return folded


def fold_state(graph_state: GraphState) -> Dict[str, Any]:
    """The workflow state of a live graph: inputs plus every completed node's delta."""
    order = [node.node_id for node in graph_state.execution_order]
    return _fold(graph_state.task, order, graph_state.results)


def fold_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """The workflow state recorded in a persisted snapshot."""
    state = snapshot["data"]["state"]
    return _fold(
        state.get("current_task"),
        state.get("execution_order") or [],
        state.get("node_results") or {},
    )


def node_spans(graph_state: GraphState) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Each completed node's spans, in execution order."""
    order = [node.node_id for node in graph_state.execution_order]
    _inputs, deltas = _deltas(graph_state.task, order, graph_state.results)
    return [(node_id, list(delta.get("spans", []))) for node_id, delta in deltas]


def next_nodes(snapshot: Optional[Dict[str, Any]]) -> List[str]:
    """The nodes a resume would run next, from a persisted snapshot."""
    if not snapshot:
        return []
    return list(snapshot["data"]["state"].get("next_nodes_to_execute") or [])


def pending_interrupts(snapshot: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Unanswered interrupts recorded in a persisted snapshot."""
    if not snapshot:
        return []
    internal = snapshot["data"]["state"].get("_internal_state") or {}
    interrupt_state = internal.get("interrupt_state") or {}
    if not interrupt_state.get("activated"):
        return []
    recorded = (interrupt_state.get("interrupts") or {}).values()
    return [i for i in recorded if i.get("response") is None]


def _answered_interrupt_state(payload: Any) -> Optional[Dict[str, Any]]:
    """The interrupt state of a multi-agent snapshot that is activated but fully answered."""
    if not isinstance(payload, dict) or payload.get("scope") != "multiAgent":
        return None
    state = (payload.get("data") or {}).get("state") or {}
    interrupt_state = (state.get("_internal_state") or {}).get("interrupt_state")
    if not isinstance(interrupt_state, dict) or interrupt_state.get("activated") is not True:
        return None
    recorded = (interrupt_state.get("interrupts") or {}).values()
    if any(interrupt.get("response") is None for interrupt in recorded):
        return None
    return interrupt_state


class ResumableStorage:
    """A Strands ``Storage`` whose reads let a graph resume after an answered interrupt.

    Strands 1.57.2 saves the graph snapshot in ``AfterNodeCallEvent``, from the
    ``finally`` block of ``Graph._execute_node``, but calls
    ``_interrupt_state.deactivate()`` in ``Graph._execute_graph`` only after the
    whole step has completed. The snapshot saved when the node that consumed the
    traveler's answer completes therefore still has an activated interrupt state
    in which every interrupt is answered. A worker killed inside the next node
    (``hold`` after a CONFIRM resume, ``prepare_hold`` after a REVIEW resume)
    leaves that snapshot as the latest. Restoring it, Strands demands interrupt
    responses again: replaying the task raises ``TypeError``, and resending the
    answers reports ``completed`` without running anything.

    ``read`` rewrites such a snapshot's interrupt state to the deactivated shape
    Strands itself saves one node later (``activated: false``, empty
    ``interrupts`` and ``context``), so the restored graph resumes from
    ``next_nodes_to_execute``. Every other byte string, including snapshots with
    an unanswered interrupt, is returned unchanged. ``write``, ``delete`` and
    ``list`` delegate unchanged.

    Args:
        storage: The storage that holds the snapshots.
    """

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    async def write(self, key: str, data: bytes) -> None:
        """Store ``data`` under ``key`` in the wrapped storage."""
        await self._storage.write(key, data)

    async def read(self, key: str) -> Optional[bytes]:
        """Read ``key``, deactivating a fully answered interrupt state.

        Args:
            key: The storage key to read.

        Returns:
            The stored bytes, re-serialized only when the interrupt state was
            rewritten; None when nothing is stored under ``key``.
        """
        data = await self._storage.read(key)
        if data is None:
            return None
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return data
        interrupt_state = _answered_interrupt_state(payload)
        if interrupt_state is None:
            return data
        interrupt_state.update(activated=False, interrupts={}, context={})
        logger.info("key=<%s> | deactivated an answered interrupt state on read", key)
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    async def delete(self, key: str) -> None:
        """Delete ``key`` from the wrapped storage."""
        await self._storage.delete(key)

    async def list(self, query: str = "") -> List[str]:
        """List the wrapped storage's keys that start with ``query``."""
        return await self._storage.list(query)


class Step:
    """One deterministic workflow step, in the AgentBase shape Strands Graph runs."""

    def __init__(self, node_id: str, nodes: WorkflowNodes, run: RunContext) -> None:
        self.node_id = node_id
        self._run_node = getattr(nodes, node_id)
        self._run = run
        self.graph: Optional[Graph] = None

    async def stream_async(
        self, prompt: Any = None, **kwargs: Any
    ) -> AsyncIterator[Dict[str, Any]]:
        """Run the step on the folded state and yield its delta as the node result."""
        if self.graph is None:
            raise RuntimeError(f"Step {self.node_id} is not attached to a graph")
        state = fold_state(self.graph.state)
        before = len(state["activities"])
        output = await self._run_node(state, {"configurable": self._run.configurable()})
        spans = list(output.get("activities", []))[before:]
        delta = {key: value for key, value in output.items() if key != "activities"}
        text = json.dumps({"state": delta, "spans": spans})
        message = {"role": "assistant", "content": [{"text": text}]}
        yield {"result": AgentResult(
            stop_reason="end_turn", message=message, metrics=EventLoopMetrics(), state={},
        )}

    async def invoke_async(self, prompt: Any = None, **kwargs: Any) -> AgentResult:
        """Run the step and return its result."""
        result = None
        async for event in self.stream_async(prompt, **kwargs):
            result = event["result"]
        return result

    def __call__(self, prompt: Any = None, **kwargs: Any) -> AgentResult:
        """Steps only run inside the graph's async loop."""
        raise RuntimeError("Workflow steps run only inside the graph's async loop")


def _last_completed(graph_state: GraphState) -> Optional[str]:
    order = graph_state.execution_order
    return order[-1].node_id if order else None


class ReviewGate(HookProvider):
    """Pause the graph where the traveler must answer before work continues.

    Every check runs before a node starts and reads which node completed last,
    so a pause "after" a node sits before whichever node the routing picks next.

    - A fresh run stops with ``REVIEW_INTERRUPT`` once ``search`` has completed
      when ``routing.pauses_after_search`` says so: the traveler asked for
      review, or the request is the canonical recovery. This matches LangGraph's
      ``interrupt_after=["search"]``; a route without search never stops there.
    - A fresh run stops with ``CONFIRM_INTERRUPT`` before ``prepare_hold`` on
      every route, so nothing reaches the hold without the traveler's answer.
    - The traveler's resume is the answer and confirms the hold, so a resumed
      run passes both gates.
    - ``pause_after`` stops once that node has completed, for the scripted
      proofs. Resuming with the answer passes it. A replay of the task from a
      snapshot whose last completed node is ``pause_after`` stops there again,
      so pass it only to the invocation meant to pause.
    """

    def __init__(self, run: RunContext) -> None:
        self._run = run

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Gate every node before it starts."""
        registry.add_callback(BeforeNodeCallEvent, self._before_node)

    def _wants_review(self, graph_state: GraphState) -> bool:
        query = str(fold_state(graph_state).get("query") or "")
        return pauses_after_search(query, self._run.review_requested)

    def _before_node(self, event: BeforeNodeCallEvent) -> None:
        run = self._run
        after = _last_completed(event.source.state)
        if run.pause_after and after == run.pause_after:
            event.interrupt(
                f"pause_after_{run.pause_after}", reason=f"Paused after {run.pause_after}."
            )
        if run.traveler_confirmed:
            return
        if after == "search" and self._wants_review(event.source.state):
            event.interrupt(
                REVIEW_INTERRUPT, reason="Review the shortlist before the run continues."
            )
        if event.node_id == "prepare_hold":
            event.interrupt(
                CONFIRM_INTERRUPT, reason="Confirm before a courtesy hold is requested."
            )


def build_graph(nodes: WorkflowNodes, run: RunContext, session_manager: Any) -> Graph:
    """Wire the steps with today's routing, the review gate, and snapshot persistence.

    Args:
        nodes: The steps and their external boundaries.
        run: This invocation's context.
        session_manager: A ``SnapshotSessionManager`` over the run's storage.

    Returns:
        A graph whose node results are saved after every node.
    """
    def intent(state: GraphState) -> str:
        return str(fold_state(state).get("intent") or "search")

    def recovery(state: GraphState) -> bool:
        return is_recovery_request(str(fold_state(state).get("query") or ""))

    steps = [Step(node_id, nodes, run) for node_id in NODE_IDS]
    builder = GraphBuilder()
    for step in steps:
        builder.add_node(step, step.node_id)
    builder.set_entry_point("classify")
    builder.add_edge("classify", "search", condition=lambda s: intent(s) in ("search", "plan"))
    builder.add_edge("classify", "availability", condition=lambda s: intent(s) == "availability")
    builder.add_edge("classify", "memory_recall", condition=lambda s: intent(s) == "memory_recall")
    builder.add_edge("search", "availability", condition=lambda s: intent(s) == "plan")
    builder.add_edge("search", "synthesize", condition=lambda s: intent(s) != "plan")
    builder.add_edge("availability", "prepare_hold", condition=recovery)
    builder.add_edge("availability", "synthesize", condition=lambda s: not recovery(s))
    builder.add_edge("prepare_hold", "hold")
    builder.add_edge("hold", "synthesize")
    builder.add_edge("memory_recall", "synthesize")
    builder.set_graph_id(GRAPH_ID)
    builder.set_max_node_executions(MAX_NODE_EXECUTIONS)
    builder.set_session_manager(session_manager)
    builder.set_hook_providers([ReviewGate(run)])
    graph = builder.build()
    for step in steps:
        step.graph = graph
    return graph
