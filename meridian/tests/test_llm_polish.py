"""Regression tests for complete concierge polish responses."""

from backend import llm_polish


class _FakeBedrockClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def converse(self, **kwargs):
        self.requests.append(kwargs)
        return next(self.responses)


def test_polish_retries_when_model_stops_mid_reply(monkeypatch) -> None:
    client = _FakeBedrockClient(
        [
            {
                "stopReason": "max_tokens",
                "output": {
                    "message": {
                        "content": [{"text": "This reply ends in the middle"}]
                    }
                },
            },
            {
                "stopReason": "end_turn",
                "output": {
                    "message": {
                        "content": [{"text": "This reply is complete."}]
                    }
                },
            },
        ]
    )
    monkeypatch.setattr(llm_polish, "_bedrock_client", client)
    monkeypatch.setattr(
        llm_polish,
        "_candidate_models",
        lambda: ["model-one", "model-two"],
    )

    result = llm_polish._polish_sync("Plan my trip", "Deterministic facts.")

    assert result.text == "This reply is complete."
    assert result.model_id == "model-two"
    assert client.requests[0]["inferenceConfig"]["maxTokens"] == 1200


def test_polish_falls_back_to_facts_when_every_reply_is_incomplete(
    monkeypatch,
) -> None:
    client = _FakeBedrockClient(
        [
            {
                "stopReason": "end_turn",
                "output": {
                    "message": {
                        "content": [{"text": "Still missing its ending"}]
                    }
                },
            }
        ]
    )
    monkeypatch.setattr(llm_polish, "_bedrock_client", client)
    monkeypatch.setattr(llm_polish, "_candidate_models", lambda: ["model-one"])

    result = llm_polish._polish_sync("Plan my trip", "Deterministic facts.")

    assert result.text == "Deterministic facts."
    assert result.model_id is None
    assert "incomplete-looking" in (result.note or "")
