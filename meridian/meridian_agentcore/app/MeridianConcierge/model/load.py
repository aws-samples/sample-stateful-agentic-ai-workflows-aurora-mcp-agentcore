import os

from strands.models.bedrock import BedrockModel

DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-4-6"


def load_model() -> BedrockModel:
    """Get Bedrock model client using IAM credentials."""
    return BedrockModel(model_id=os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID))
