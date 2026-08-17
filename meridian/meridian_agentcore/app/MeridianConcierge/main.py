import json
from typing import Any

from strands import Agent
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from model.load import load_model

app = BedrockAgentCoreApp()
log = app.logger

DEFAULT_SYSTEM_PROMPT = """
You are Meridian's production travel concierge running inside Amazon Bedrock
AgentCore Runtime. You receive an authenticated traveler request, authorized
memory context, and live trip candidates already returned by Meridian's
AgentCore Gateway.

Write a concise, traveler-facing recommendation grounded only in those inputs.
Explain the most relevant preference match when evidence is present. Never
invent seats, flight times, hotel confirmations, loyalty benefits, prices, or
availability. If no candidates are supplied, say that no exact match was found.
Use two to four sentences and do not add a heading.
"""

_agent = None


def get_or_create_agent():
    global _agent
    if _agent is None:
        _agent = Agent(
            model=load_model(),
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            tools=[],
        )
    return _agent


@app.entrypoint
async def invoke(payload, context):
    if payload.get("event") != "concierge_turn":
        yield json.dumps(
            {
                "message": "Unsupported Meridian Runtime event.",
                "recommended_package_ids": [],
                "follow_ups": [],
            }
        )
        return

    agent = get_or_create_agent()
    candidates = payload.get("candidates") or []
    prompt = (
        f"Traveler request:\n{payload.get('prompt', '')}\n\n"
        f"Authorized traveler context:\n{payload.get('memory_context', '')}\n\n"
        "Live candidate packages:\n"
        f"{json.dumps(candidates, ensure_ascii=True)}"
    )

    chunks: list[str] = []
    stream = agent.stream_async(prompt)
    async for event in stream:
        if "data" in event and isinstance(event["data"], str):
            chunks.append(event["data"])

    message = "".join(chunks).strip()
    if not message:
        message = "I could not produce a grounded recommendation from the live options."

    yield json.dumps(
        {
            "message": message,
            "recommended_package_ids": [
                candidate.get("package_id")
                for candidate in candidates
                if candidate.get("package_id")
            ],
            "follow_ups": [
                "Compare the top options",
                "Check duration availability",
                "Explain the preference match",
            ],
        }
    )


if __name__ == "__main__":
    app.run()
