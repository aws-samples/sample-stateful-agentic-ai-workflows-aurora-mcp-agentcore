"""A persistence and pause/resume example in LangGraph.

It shows one thing: a graph that pauses, saves its state in Amazon Aurora and resumes from
that state in whichever process makes the next call. It is not Meridian's governed workflow,
which is the Strands Graph that Phase 5 runs on AgentCore Runtime. The nodes only have the
same names: classify the request, search for options, pause so a person can review them,
then "hold". Search makes up two options from the request, and the hold node marks the
first one as held after the resume, even for an ordinary question. No real hold is placed,
no Gateway tool is called, and there is no worker lease or recovery. The pause is
``interrupt_after`` on the search node, so the checkpoint written there is what a later call
resumes from.

``build_graph`` takes any LangGraph checkpointer. ``build_aurora_graph`` wires
in ``AuroraDataApiSaver`` so the saved step lives in Amazon Aurora.
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
