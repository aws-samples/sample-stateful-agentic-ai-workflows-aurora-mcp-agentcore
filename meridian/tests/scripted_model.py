"""A Strands model that plays a fixed script, so agent loops run offline and deterministically."""

from __future__ import annotations

import json
from typing import Any, AsyncIterable

from strands.models import Model


class ScriptedModel(Model):
    """Each ``turns`` entry is one model response: a tool call or the final text.

    ``{"kind": "tool", "id": "t1", "name": "price_range", "input": {...}}`` or
    ``{"kind": "text", "text": "..."}``. ``calls`` records the tool names and system prompt
    the agent offered on every model call.
    """

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> dict[str, Any]:
        return {"model_id": "scripted"}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError
        yield

    async def stream(
        self, messages, tool_specs=None, system_prompt=None, **kwargs
    ) -> AsyncIterable[dict[str, Any]]:
        self.calls.append({
            "tools": [spec["name"] for spec in (tool_specs or [])],
            "system": system_prompt,
        })
        if not self.turns:
            raise AssertionError("the agent called the model more often than the script allows")
        turn = self.turns.pop(0)
        yield {"messageStart": {"role": "assistant"}}
        if turn["kind"] == "text":
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": turn["text"]}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        else:
            start = {"toolUse": {"toolUseId": turn["id"], "name": turn["name"]}}
            yield {"contentBlockStart": {"start": start}}
            delta = {"toolUse": {"input": json.dumps(turn["input"])}}
            yield {"contentBlockDelta": {"delta": delta}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                            "metrics": {"latencyMs": 1}}}
