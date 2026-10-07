"""The workflow pauses after search and resumes into the hold."""

from langgraph.checkpoint.memory import InMemorySaver

from examples.langgraph.pause_resume_graph import build_graph

CONFIG = {"configurable": {"thread_id": "pause-resume-unit"}}


async def test_pauses_after_search_then_resumes_into_the_hold():
    graph = build_graph(InMemorySaver())

    paused = await graph.ainvoke({"query": "book a trip to Lisbon"}, CONFIG)

    assert paused["intent"] == "booking"
    assert paused["options"]
    assert "held" not in paused
    assert (await graph.aget_state(CONFIG)).next == ("hold",)

    resumed = await graph.ainvoke(None, CONFIG)

    assert resumed["held"] == paused["options"][0]
    assert (await graph.aget_state(CONFIG)).next == ()


async def test_a_question_is_classified_and_still_pauses():
    graph = build_graph(InMemorySaver())

    paused = await graph.ainvoke({"query": "what is the baggage limit"}, CONFIG)

    assert paused["intent"] == "question"
    assert (await graph.aget_state(CONFIG)).next == ("hold",)


def test_the_aurora_graph_compiles_with_the_data_api_saver():
    from unittest.mock import Mock

    from examples.langgraph.aurora_dataapi_saver import AuroraDataApiSaver
    from examples.langgraph.pause_resume_graph import build_aurora_graph

    graph = build_aurora_graph(Mock())

    assert isinstance(graph.checkpointer, AuroraDataApiSaver)
