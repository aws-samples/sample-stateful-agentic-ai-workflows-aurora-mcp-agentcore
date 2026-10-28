"""Claude Sonnet 5.5 on Amazon Bedrock, built the one way Bedrock accepts it.

Measured on this model: a ``temperature`` is a ValidationException, ``thinking: disabled`` is
rejected with a 400, and a forced ``toolChoice`` is rejected. So none is sent: tool choice
stays automatic and the sampling defaults stay the model's. The one thing that varies is
the thinking setting, and it lives in ``REQUEST_FIELDS`` below, nowhere else.

The Concierge Runtime cannot import ``backend/``, so ``meridian_agentcore/app/MeridianConcierge/
model/load.py`` repeats this contract; ``tests/test_bedrock_model.py`` fails if they differ.

AWS docs:
  - Model IDs and inference profiles:
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html
  - Cross-Region inference:
    https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html
"""

import copy
from typing import Any, Dict, Optional

from botocore.config import Config
from strands.models import BedrockModel

from backend.config import BedrockConfig, config

DEFAULT_MODEL_ID = BedrockConfig.DEFAULT_MODEL_ID

# The model request fields beyond the Converse basics: adaptive thinking at effort medium, the
# owner's choice after a live measurement (3.97 s mean a turn against 4.85 s with no thinking
# field, same tools and stop reasons). It is the "adaptive thinking, effort medium" candidate
# of scripts/smoke_bedrock_model.py, which is run before each release; change this value, and
# the same constant in the Runtime's load.py, only to what that smoke accepted. Thinking
# tokens count against max_tokens, so the 16,000-token cap leaves room to think and answer.
REQUEST_FIELDS: Dict[str, Any] = {
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "medium"},
}

# Throttling and transient errors are retried by botocore with client-side rate limiting, up to
# MAX_ATTEMPTS attempts in all (botocore's total_max_attempts; its max_attempts counts retries
# only, which would make one more). A persistent failure is raised, never answered by a
# different model.
MAX_ATTEMPTS = 5


def request_fields() -> Dict[str, Any]:
    """A copy of the model request fields, for callers that build their own request."""
    return copy.deepcopy(REQUEST_FIELDS)


def bedrock_model(model_id: Optional[str] = None, *, max_tokens: int = 16000) -> BedrockModel:
    """Build the Strands model for Claude Sonnet 5.5 (or the model ``BEDROCK_MODEL_ID`` names).

    Args:
        model_id: Overrides ``BEDROCK_MODEL_ID``.
        max_tokens: Output budget for one model call.

    Returns:
        A Strands ``BedrockModel`` with adaptive retries and no parameter Sonnet 5.5 rejects.
    """
    options: Dict[str, Any] = {}
    fields = request_fields()
    if fields:
        options["additional_request_fields"] = fields
    return BedrockModel(
        model_id=model_id or config.bedrock.model_id,
        region_name=config.bedrock.region,
        max_tokens=max_tokens,
        boto_client_config=Config(retries={"mode": "adaptive", "total_max_attempts": MAX_ATTEMPTS}),
        **options,
    )
