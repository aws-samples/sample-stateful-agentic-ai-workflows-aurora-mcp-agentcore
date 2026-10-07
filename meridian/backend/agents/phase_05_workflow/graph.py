"""The Phase 5 workflow as a Strands Graph whose state lives in Aurora.

Each node is a deterministic step. Its result message carries a JSON delta:
the state keys it changed and the spans it emitted. Strands persists every
node result in the graph snapshot, so folding the snapshot rebuilds the
workflow state in any process, with nothing kept in memory between runs.
"""

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from strands.agent.agent_result import AgentResult
from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import BeforeNodeCallEvent
from strands.multiagent import GraphBuilder
from strands.multiagent.base import NodeResult, Status
from strands.multiagent.graph import Graph, GraphState
from strands.telemetry.metrics import EventLoopMetrics

from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from backend.agents.phase_05_workflow.routing import is_recovery_request, pauses_after_search

GRAPH_ID = "phase5"
NODE_IDS = (
    "classify", "search", "availability", "memory_recall", "prepare_hold", "hold", "synthesize",
)
REVIEW_INTERRUPT = "review_shortlist"
CONFIRM_INTERRUPT = "confirm_hold"
FOLLOWING_NODE = {
    "search": "availability",
    "availability": "prepare_hold",
    "prepare_hold": "hold",
    "hold": "synthesize",
}
# Generous for an acyclic graph: a replayed step counts as another execution.
MAX_NODE_EXECUTIONS = 3 * len(NODE_IDS)


@dataclass
class RunContext:
    """What one invocation knows that the snapshot does not.

    Attributes:
        thread_id: The workflow thread, also the Strands session id.
        execution_id: The execution holding the lease.
        traveler_confirmed: The traveler resumed after reviewing the plan.
        pause_after_search: Stop for review before availability.
        pause_after: Stop after this node; used by the scripted proofs.
    """

    thread_id: str
    execution_id: Optional[str]
    traveler_confirmed: bool
    pause_after_search: bool
    pause_after: Optional[str] = None

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
        text = json.dumps({"state": delta, "spans": spans}, default=str)
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


class ReviewGate(HookProvider):
    """Pause the graph where the traveler must answer before work continues.

    A fresh run stops before ``prepare_hold`` on every route, and before
    availability when review was asked for or the request is the canonical
    recovery (``pauses_after_search``). The traveler's resume is the
    answer and confirms the hold, so a resumed run passes both gates.
    ``pause_after`` stops after one named node, for the scripted proofs.
    """

    def __init__(self, run: RunContext) -> None:
        self._run = run

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Gate every node before it starts."""
        registry.add_callback(BeforeNodeCallEvent, self._before_node)

    def _wants_review(self, event: BeforeNodeCallEvent) -> bool:
        query = str(fold_state(event.source.state).get("query") or "")
        return pauses_after_search(query, self._run.pause_after_search)

    def _before_node(self, event: BeforeNodeCallEvent) -> None:
        run = self._run
        if run.pause_after and event.node_id == FOLLOWING_NODE.get(run.pause_after):
            event.interrupt(
                f"pause_after_{run.pause_after}", reason=f"Paused after {run.pause_after}."
            )
        if run.traveler_confirmed:
            return
        if event.node_id == "availability" and self._wants_review(event):
            event.interrupt(
                REVIEW_INTERRUPT, reason="Review the shortlist before availability and any hold."
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
