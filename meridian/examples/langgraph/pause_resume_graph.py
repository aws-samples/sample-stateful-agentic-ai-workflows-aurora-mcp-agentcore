"""A pause and resume workflow in LangGraph.

This is the LangGraph counterpart of the Strands Graph that Phase 5 runs on
AgentCore Runtime. The nodes are the same shape: classify the request, search
for options, pause so a person can review them, then place a hold. The pause
is ``interrupt_after`` on the search node, so the checkpoint written there is
what a later call resumes from, whichever process makes that call.

``build_graph`` takes any LangGraph checkpointer. ``build_aurora_graph`` wires
in ``AuroraDataApiSaver`` so the saved step lives in AWS Aurora.
"""

from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from examples.langgraph.aurora_dataapi_saver import AuroraDataApiSaver


class WorkflowState(TypedDict, total=False):
    """What each step reads and writes."""

    query: str
    intent: str
    options: list[str]
    held: str


def classify(state: WorkflowState) -> dict[str, Any]:
    """Decide whether the request is a booking or a question."""
    wants_booking = "book" in state["query"].lower()
    return {"intent": "booking" if wants_booking else "question"}


def search(state: WorkflowState) -> dict[str, Any]:
    """Find options for the request."""
    return {"options": [f"{state['intent']} option A", f"{state['intent']} option B"]}


def hold(state: WorkflowState) -> dict[str, Any]:
    """Hold the first option once the review has passed."""
    return {"held": state["options"][0]}


def build_graph(checkpointer: BaseCheckpointSaver):
    """Compile the workflow so it pauses after search for review."""
    graph = StateGraph(WorkflowState)
    graph.add_node("classify", classify)
    graph.add_node("search", search)
    graph.add_node("hold", hold)
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "search")
    graph.add_edge("search", "hold")
    graph.add_edge("hold", END)
    return graph.compile(checkpointer=checkpointer, interrupt_after=["search"])


def build_aurora_graph(rds_data_client: Any):
    """Compile the workflow with checkpoints saved through the RDS Data API."""
    return build_graph(AuroraDataApiSaver(rds_data_client))
