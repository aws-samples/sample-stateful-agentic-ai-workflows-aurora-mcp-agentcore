"""
Configuration settings for Meridian backend.

Centralizes all configurable values that were previously hardcoded.

AWS docs (env vars used across phases):
  - Aurora + RDS Data API:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
  - Bedrock model access:
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html
"""

import os
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class SearchConfig:
    """Search-related configuration."""

    # Default result limit
    default_limit: int = 5

    # Candidate pool multiplier for semantic retrieval before reranking.
    rerank_candidate_multiplier: int = 5

    # Category keyword mappings for Phase 1/2 search
    # Only exact category names or very specific keywords should match
    # Semantic queries like "help with muscle recovery" should NOT match
    category_keywords: Dict[str, str | None] = field(default_factory=lambda: {
        "city break": "City Breaks",
        "city breaks": "City Breaks",
        "city trip": "City Breaks",
        "city trips": "City Breaks",
        "beach": "Beach & Resort",
        "resort": "Beach & Resort",
        "adventure": "Adventure & Outdoors",
        "outdoors": "Adventure & Outdoors",
        "wellness": "Wellness & Luxury",
        "luxury": "Wellness & Luxury",
        "family": "Family Trips",
        "family trip": "Family Trips",
        "business travel": "Business Travel",
        "business trip": "Business Travel",
    })


@dataclass
class AgentConfig:
    """Agent configuration by phase."""

    # Agent names and files for each phase
    search_agents: Dict[int, tuple] = field(default_factory=lambda: {
        1: ("SQLAgent", "agents/sql_01/agent.py"),
        2: ("MCPAgent", "agents/mcp_02/agent.py"),
        3: ("RetrievalAgent", "agents/retrieval_03/supervisor.py"),
    })

    booking_agents: Dict[int, tuple] = field(default_factory=lambda: {
        1: ("SQLAgent", "agents/sql_01/agent.py"),
        2: ("MCPAgent", "agents/mcp_02/agent.py"),
        3: ("BookingAgent", "agents/retrieval_03/booking_agent.py"),
    })

    # Progressive reveal delays (ms) - for demo purposes
    phase_delays: Dict[int, int] = field(default_factory=lambda: {
        1: 600,  # Slower to show process
        2: 450,
        3: 350,  # Faster (more sophisticated)
    })


@dataclass
class BedrockConfig:
    """Bedrock LLM configuration.

    Every agent in the codebase reads its model identifier from here, so the
    operator can swap models for the whole app via a single environment
    variable (``BEDROCK_MODEL_ID``) without editing eight files.

    Default is the Global cross-Region inference profile for Anthropic Claude
    Sonnet 5 (``global.anthropic.claude-sonnet-5``). Swap to
    ``global.anthropic.claude-opus-5`` for maximum quality. If you see::

        ValidationException: The provided model identifier is invalid

    that error comes from the Bedrock API itself — usually because the
    profile isn't in your account's Model access list, or your region
    doesn't route to it. Pick another profile from the Bedrock console
    and set it in ``.env``::

        BEDROCK_MODEL_ID=global.anthropic.claude-sonnet-5

    AWS docs:
      - Model access:
        https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html
      - Model IDs / inference profiles:
        https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html
      - Cross-Region inference:
        https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html

    Quick check from the shell::

        aws bedrock list-inference-profiles --region us-east-1 \\
            --query "inferenceProfileSummaries[?contains(inferenceProfileId, 'anthropic')].inferenceProfileId"
    """

    DEFAULT_MODEL_ID: str = "global.anthropic.claude-sonnet-5"

    model_id: str = field(
        default_factory=lambda: os.getenv(
            "BEDROCK_MODEL_ID",
            BedrockConfig.DEFAULT_MODEL_ID,
        )
    )
    region: str = field(
        default_factory=lambda: os.getenv(
            "BEDROCK_REGION",
            os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        )
    )


_MODEL_LABELS = {
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-haiku-4-5-20251001-v1:0": "Claude Haiku 4.5",
    "claude-opus-5": "Claude Opus 5",
}


def bedrock_model_label(model_id: str) -> str:
    """Human-readable label for Run config / health (from BEDROCK_MODEL_ID).

    Names the polish chain (Sonnet 5 -> Haiku 4.5 -> Opus 5) whatever its
    inference profile prefix or ARN. Any other model shows its own ID, so the
    UI never names a model that is not running.
    """
    profile = model_id.rsplit("/", 1)[-1]
    return _MODEL_LABELS.get(profile.split("anthropic.", 1)[-1], profile)


EMBEDDING_MODEL_ID: str = os.getenv("EMBEDDING_MODEL", "cohere.embed-v4:0")


@dataclass
class Config:
    """Main configuration container."""

    search: SearchConfig = field(default_factory=SearchConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    bedrock: BedrockConfig = field(default_factory=BedrockConfig)

    # Environment overrides
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


# Global configuration instance
config = Config()
