import copy
import os

from botocore.config import Config
from strands.models.bedrock import BedrockModel

DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-5-5"

# Repeats backend/bedrock_model.py, which this Runtime cannot import. Sonnet 5.5 rejects a
# temperature, thinking disabled and a forced tool choice, so none is sent. Adaptive thinking
# at effort medium is the owner's choice, the smoke's "adaptive thinking, effort medium".
# tests/test_bedrock_model.py fails if the two files differ.
REQUEST_FIELDS: dict = {
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "medium"},
}
MAX_ATTEMPTS = 5
# Adaptive thinking at effort medium spends output tokens before the answer. The smoke's 1024
# fits a one-word reply only; the agents run on 16000 and reply with tool results and cards.
MIN_MAX_TOKENS = 4096


def load_model(model_id: str | None = None, region_name: str | None = None) -> BedrockModel:
    """Use the configured Bedrock model with a bounded interactive-turn budget."""
    model_id = model_id or os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    if "openai.gpt-oss" in model_id:
        raise ValueError("Concierge requires a hosted model; GPT-OSS is not supported")
    max_tokens = int(os.getenv("BEDROCK_MAX_TOKENS", "16000"))
    if max_tokens < MIN_MAX_TOKENS or max_tokens > 16000:
        raise ValueError(
            f"BEDROCK_MAX_TOKENS must be between {MIN_MAX_TOKENS} and 16000, got {max_tokens}: "
            "thinking at effort medium shares this budget with the answer")
    options = {}
    if REQUEST_FIELDS:
        options["additional_request_fields"] = copy.deepcopy(REQUEST_FIELDS)
    return BedrockModel(
        model_id=model_id,
        region_name=region_name or os.getenv("AWS_REGION", "us-east-1"),
        max_tokens=max_tokens,
        boto_client_config=Config(retries={"mode": "adaptive", "total_max_attempts": MAX_ATTEMPTS}),
        **options,
    )
