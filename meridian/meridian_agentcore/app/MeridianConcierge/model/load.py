import os

from strands.models.bedrock import BedrockModel

DEFAULT_MODEL_ID = "us.openai.gpt-6-luna"


def load_model(model_id: str | None = None, region_name: str | None = None) -> BedrockModel:
    """Use the configured Bedrock model with a bounded interactive-turn budget."""
    model_id = model_id or os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    if "openai.gpt-oss" in model_id:
        raise ValueError("Concierge requires a hosted model; GPT-OSS is not supported")
    max_tokens = int(os.getenv("BEDROCK_MAX_TOKENS", "2048"))
    if max_tokens < 256 or max_tokens > 16000:
        raise ValueError("BEDROCK_MAX_TOKENS must be between 256 and 16000")
    options = {}
    if "openai.gpt-" in model_id:
        # Hosted GPT models use the Responses reasoning shape through Converse.
        options["additional_request_fields"] = {
            "reasoning": {"effort": os.getenv("BEDROCK_REASONING_EFFORT", "low")}
        }
    return BedrockModel(
        model_id=model_id,
        region_name=region_name or os.getenv("AWS_REGION", "us-east-1"),
        max_tokens=max_tokens,
        **options,
    )
