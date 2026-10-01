import os

from strands.models.bedrock import BedrockModel

DEFAULT_MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def load_model() -> BedrockModel:
    """Get Bedrock model client using IAM credentials."""
    model_id = os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    return BedrockModel(model_id=model_id, max_tokens=4096 if "haiku" in model_id else 16000)
