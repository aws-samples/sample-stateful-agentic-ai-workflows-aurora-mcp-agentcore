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
from typing import Dict, List


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
class OrderConfig:
    """Order processing configuration."""

    # Tax rate (8% = 0.08)
    tax_rate: float = 0.08

    # Shipping fee when below threshold
    shipping_fee: float = 5.99

    # Free shipping threshold
    free_shipping_threshold: float = 50.0

    # Delivery estimate range (business days)
    min_delivery_days: int = 3
    max_delivery_days: int = 5


@dataclass
class UploadConfig:
    """File upload configuration."""

    # Maximum image size in bytes (5MB)
    max_image_size: int = 5 * 1024 * 1024

    # Allowed image MIME types
    allowed_image_types: List[str] = field(default_factory=lambda: [
        "image/jpeg",
        "image/png",
        "image/webp",
    ])


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
    presenter can swap models for the entire demo via a single environment
    variable (``BEDROCK_MODEL_ID``) without editing eight files.

    Default is the Global cross-Region inference profile for Anthropic Claude
    Opus 4.8 (``global.anthropic.claude-opus-4-8``). If you see::

        ValidationException: The provided model identifier is invalid

    that error comes from the Bedrock API itself — usually because the
    profile isn't in your account's Model access list, or your region
    doesn't route to it. Pick another profile from the Bedrock console
    and set it in ``.env``::

        BEDROCK_MODEL_ID=global.anthropic.claude-sonnet-4-5-20250929-v1:0

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

    DEFAULT_MODEL_ID: str = "global.anthropic.claude-opus-4-8"

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


def bedrock_model_label(model_id: str) -> str:
    """Human-readable label for Run config / health (from BEDROCK_MODEL_ID).

    Covers the live fallback chain: Opus 4.8 -> Sonnet 4.6 -> Haiku 4.5.
    """
    mid = model_id.lower()
    if "opus-4-8" in mid or "opus-4.8" in mid:
        return "Claude Opus 4.8"
    if "sonnet-4-6" in mid or "sonnet-4.6" in mid:
        return "Claude Sonnet 4.6"
    if "sonnet-4-5" in mid or "sonnet-4.5" in mid:
        return "Claude Sonnet 4.5"
    if "haiku" in mid:
        return "Claude Haiku 4.5"
    if "anthropic" in mid and "claude" in mid:
        return "Claude (Bedrock)"
    return model_id.rsplit("/", 1)[-1] if "/" in model_id else model_id


EMBEDDING_MODEL_ID: str = os.getenv("EMBEDDING_MODEL", "cohere.embed-v4:0")


@dataclass
class Config:
    """Main configuration container."""

    search: SearchConfig = field(default_factory=SearchConfig)
    order: OrderConfig = field(default_factory=OrderConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    bedrock: BedrockConfig = field(default_factory=BedrockConfig)

    # Environment overrides
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


# Global configuration instance
config = Config()
