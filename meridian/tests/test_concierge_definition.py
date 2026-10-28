"""One definition builds the Phase 3 agent and the Runtime agent, and pins the traveler."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from strands import tool

from backend.agents import concierge_definition as definition
from tests.scripted_model import ScriptedModel

SOURCE = Path(definition.__file__)


def test_the_contract_tuples_are_exactly_the_agreed_names():
    assert definition.CATALOG_TOOLS == (
        "search_trips", "compare_packages", "price_range", "region_inventory",
        "currency_convert", "hybrid_trip_search")
    assert definition.DETAIL_TOOLS == ("get_package_details",)
    assert definition.TRAVELER_TOOLS == (
        "loyalty_balance", "create_courtesy_hold", "confirm_booking", "release_hold")
    assert definition.PHASE3_TOOLS == definition.CATALOG_TOOLS + definition.DETAIL_TOOLS + (
        "loyalty_balance",)
    assert definition.PHASE4_TOOLS == (
        definition.CATALOG_TOOLS + definition.DETAIL_TOOLS + definition.TRAVELER_TOOLS)


def test_every_tool_has_an_argument_contract_and_every_traveler_tool_takes_the_pinned_one():
    assert set(definition.TOOL_ARGUMENTS) == set(definition.PHASE4_TOOLS)
    for name in definition.TRAVELER_TOOLS:
        assert definition.TRAVELER_ARGUMENT in definition.TOOL_ARGUMENTS[name], name
    for name in set(definition.PHASE4_TOOLS) - set(definition.TRAVELER_TOOLS):
        assert definition.TRAVELER_ARGUMENT not in definition.TOOL_ARGUMENTS[name], name


def test_the_definition_imports_nothing_from_backend_because_the_runtime_cannot():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add((node.module or "").split(".")[0])
    assert "backend" not in roots
    assert roots <= {"__future__", "json", "typing", "strands"}


def test_phase_three_names_every_read_tool_and_offers_no_write():
    text = definition.system_prompt(3)
    for name in definition.PHASE3_TOOLS:
        assert name in text, name
    for name in definition.WRITE_TOOLS:
        assert name not in text, name
    assert "Production" in text
    assert set(definition.PHASE3_TOOLS).isdisjoint(definition.WRITE_TOOLS)


def test_phase_three_assumes_no_profile_and_no_memory():
    text = definition.system_prompt(3).lower()
    for phrase in ("preference", "traveler detail", "traveler context", "already discussed",
                   "remember", "saved"):
        assert phrase not in text, phrase
    assert text.count("memory") == 1 and "no memory of earlier turns" in text
    assert "profile" not in text


def test_phase_four_adds_the_governed_write_rules():
    text = definition.system_prompt(4)
    for name in definition.PHASE4_TOOLS:
        if name != "release_hold":
            assert name in text, name
    assert "final for the current turn only" in text
    assert "remembered" in text and "saved-preference" in text
    assert "authorized traveler context" in text and "already discussed" in text
    assert ("In this phase, grounding also covers the authorized traveler context and "
            "remembered details, not only tool results and this request.") in " ".join(
                text.split())
    assert "Two more tools write" in text
    assert "release_hold" not in text
    assert definition.PROMPT_CORE in text and definition.PROMPT_CORE in definition.system_prompt(3)


@pytest.mark.parametrize("phase", [3, 4])
def test_a_refusal_answer_is_final_in_both_prompts(phase):
    text = " ".join(definition.system_prompt(phase).split())
    assert "traveler_not_authorized" in text and '"final": true' in text
    assert "do not call that tool again in this turn" in text


def test_an_unknown_phase_is_refused_with_the_valid_ones():
    with pytest.raises(ValueError, match="phase 5.*3 or 4"):
        definition.system_prompt(5)


def test_the_prompts_follow_the_copy_rules():
    for phase in (3, 4):
        text = definition.system_prompt(phase)
        assert not any(ch in text for ch in "\u00b7\u2014\u2013"), phase
        assert "demo" not in text.lower(), phase


def test_short_name_strips_one_gateway_target_prefix():
    assert definition.short_name("MeridianHolds___loyalty_balance") == "loyalty_balance"
    assert definition.short_name("hybrid_trip_search") == "hybrid_trip_search"
    assert definition.short_name("MeridianHolds___batch___confirm") == "batch___confirm"


def test_pin_traveler_argument_overwrites_what_the_model_sent():
    arguments = {"travelerId": "trv_someone_else", "program": "Marriott Bonvoy"}
    definition.pin_traveler_argument(arguments, "trv_pinned")
    assert arguments == {"travelerId": "trv_pinned", "program": "Marriott Bonvoy"}


def _event(name, arguments):
    return SimpleNamespace(tool_use={"name": name, "toolUseId": "t1", "input": arguments})


@pytest.mark.parametrize("name", [
    "loyalty_balance", "MeridianHolds___loyalty_balance", "create_courtesy_hold",
    "MeridianHolds___confirm_booking", "release_hold"])
def test_the_hook_pins_the_traveler_on_every_traveler_tool(name):
    hook = definition.TravelerPinHook("trv_pinned")
    event = _event(name, {"travelerId": "trv_decoy"})
    hook.before(event)
    assert event.tool_use["input"]["travelerId"] == "trv_pinned"


@pytest.mark.parametrize("tool_use", [
    {"name": "loyalty_balance", "toolUseId": "t1"},
    {"name": "loyalty_balance", "toolUseId": "t1", "input": None},
])
def test_the_hook_pins_a_missing_or_null_input(tool_use):
    event = SimpleNamespace(tool_use=tool_use)
    definition.TravelerPinHook("trv_pinned").before(event)
    assert event.tool_use["input"] == {"travelerId": "trv_pinned"}


@pytest.mark.parametrize("bad", [["trv_decoy"], "trv_decoy", 7])
def test_the_hook_refuses_an_input_that_is_not_an_object(bad):
    event = _event("MeridianHolds___confirm_booking", bad)
    with pytest.raises(definition.ToolInputNotAnObject, match="confirm_booking.*not an object"):
        definition.TravelerPinHook("trv_pinned").before(event)
    assert event.tool_use["input"] is bad


def test_the_hook_pins_every_tool_whose_contract_takes_the_traveler():
    pinned = {n for n, args in definition.TOOL_ARGUMENTS.items()
              if definition.TRAVELER_ARGUMENT in args}
    assert pinned == set(definition.TRAVELER_TOOLS)
    for name in pinned:
        event = _event(name, {})
        definition.TravelerPinHook("trv_pinned").before(event)
        assert event.tool_use["input"] == {"travelerId": "trv_pinned"}, name


def test_the_hook_adds_the_pin_when_the_model_left_it_out_and_ignores_other_tools():
    hook = definition.TravelerPinHook("trv_pinned")
    missing = _event("loyalty_balance", {"program": "x"})
    hook.before(missing)
    assert missing.tool_use["input"] == {"program": "x", "travelerId": "trv_pinned"}
    search = _event("hybrid_trip_search", {"query": "Tokyo"})
    hook.before(search)
    assert search.tool_use["input"] == {"query": "Tokyo"}


def test_a_running_agent_hands_the_tool_the_pinned_traveler_not_the_models():
    seen = []

    @tool
    def loyalty_balance(travelerId: str, program: str) -> dict:
        """Loyalty balance.

        Args:
            travelerId: traveler id.
            program: program name.
        """
        seen.append(travelerId)
        return {"points_balance": 1}

    model = ScriptedModel([
        {"kind": "tool", "id": "t1", "name": "loyalty_balance",
         "input": {"travelerId": "trv_decoy", "program": "Bonvoy"}},
        {"kind": "text", "text": "You have 1 point."},
    ])
    agent = definition.build_agent(
        model=model, tools=[loyalty_balance], hooks=[definition.TravelerPinHook("trv_pinned")],
        phase=3, callback_handler=None)

    reply = asyncio.run(agent.invoke_async("How many points do I have?"))

    assert seen == ["trv_pinned"]
    assert "1 point" in str(reply)


def test_build_agent_uses_the_phase_prompt_and_appends_extra_instructions():
    model = ScriptedModel([{"kind": "text", "text": "Hello."}])
    agent = definition.build_agent(
        model=model, tools=[], hooks=[], phase=4, extra_instructions="Turn rule: be brief.",
        callback_handler=None)
    asyncio.run(agent.invoke_async("hi"))
    system = model.calls[0]["system"]
    assert system.startswith(definition.system_prompt(4))
    assert system.endswith("Turn rule: be brief.")


def test_build_agent_refuses_an_unknown_phase():
    with pytest.raises(ValueError, match="phase 9"):
        definition.build_agent(model=ScriptedModel([]), tools=[], hooks=[], phase=9)


def test_tool_payload_reads_every_shape_a_tool_result_takes():
    text = {"status": "success", "content": [{"text": '{"packages": [1]}'}]}
    assert definition.tool_payload(text) == {"packages": [1]}
    split = {"content": [{"text": '{"packages":'}, {"text": " [2]}"}]}
    assert definition.tool_payload(split) == {"packages": [2]}
    block = {"content": [{"json": {"packages": [3]}}]}
    assert definition.tool_payload(block) == {"packages": [3]}
    structured = {"content": [], "structuredContent": {"packages": [4]}}
    assert definition.tool_payload(structured) == {"packages": [4]}
    wrapped = {"content": [], "structuredContent": {"result": [{"package_id": "A"}]}}
    assert definition.tool_payload(wrapped) == [{"package_id": "A"}]
    assert definition.tool_payload({"content": [{"text": "not json"}]}) is None
    assert definition.tool_payload({}) is None


def test_package_rows_come_only_from_search_and_compare_results_with_ids():
    rows = {"packages": [{"package_id": "A", "name": "a"}, {"name": "no id"}, "junk"]}
    assert definition.package_rows("hybrid_trip_search", rows) == [{"package_id": "A", "name": "a"}]
    assert definition.package_rows("search_trips", rows) == [{"package_id": "A", "name": "a"}]
    assert definition.package_rows("compare_packages", [{"package_id": "B"}]) == [
        {"package_id": "B"}]
    assert definition.package_rows("price_range", rows) == []
    assert definition.package_rows("hybrid_trip_search", None) == []
