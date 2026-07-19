"""
Concierge-tone polish over deterministic tool outputs.

The MCP path produces precise but dry readouts ("FX via meridian-concierge
MCP: 2500 USD ≈ 2300 EUR"). On stage we want longer, narrative replies
that read like a real travel concierge. This module wraps a Bedrock
Converse call (Claude Sonnet 5 by default, with fallback to Haiku 4.5
and Opus 4.8) around the deterministic facts so the user sees a richer
answer without the agent hallucinating numbers - all factual content
comes from the tool result that we feed verbatim into the system prompt.

Returns a `PolishResult` carrying both the text AND metadata about which
model responded (or which error blocked the polish), so the caller can
log the model id into the trace and surface failures to the user instead
of silently falling back to the dry deterministic output.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import List, Optional

import boto3
from botocore.config import Config

from backend.config import config

logger = logging.getLogger(__name__)


_CONCIERGE_SYSTEM = (
    "You are Meridian, a high-end travel concierge speaking directly to the "
    "traveler. Your job RIGHT NOW is to turn raw tool output from our internal "
    "agent into a warm, helpful, well-structured reply.\n\n"
    "Hard rules:\n"
    "- Use ONLY the facts in the TOOL OUTPUT. Do not invent prices, dates, "
    "destinations, points balances, rates, similarity scores, or "
    "preferences. If a number or product name is in the output, reuse it "
    "verbatim. If something is unknown, say so honestly.\n"
    "- Keep the response between 3 and 7 sentences (about 70-180 words).\n"
    "- Open with the direct answer to the traveler's question, not preamble.\n"
    "- The TOOL OUTPUT already lists products in RANKED order (best match "
    "first — the list is sorted by the reranker). LEAD WITH THE FIRST trip in "
    "that list and present it as the top recommendation; do NOT promote a "
    "lower-ranked trip above it. You may mention a later trip as a secondary "
    "option ('if you'd lean more into X…'), but never call a lower-ranked "
    "item 'the standout' or say it 'leads despite a lower score' — that "
    "contradicts the ranking the audience can see on the cards. Briefly "
    "explain the top pick's fit using the structured fields (price, "
    "semantic_match, trip_type), then end with a focused follow-up question.\n"
    "- When the TOOL OUTPUT lists 'Traveler preferences applied to this turn', "
    "weave 3-5 of them naturally into the reply, prioritizing whichever "
    "preferences actually shape THIS recommendation. Examples: 'your "
    "no_red_eye rule out of JFK', 'within the boutique-over-chain lodging "
    "style you favor', 'I noted your shellfish allergy on dining picks', "
    "'we'll route on Marriott Bonvoy / United MileagePlus where it earns', "
    "'kept the Tokyo Oct 12-19 thread alive', 'sized for a 2-traveler party'. "
    "Vary which facts you cite turn-to-turn so the relationship feels "
    "lived-in - never repeat the same 1-2 facts every time. If the "
    "traveler is explicitly asking about prior turns or what was discussed, "
    "you may also briefly cite recent recalled trips. Do NOT enumerate "
    "the full list as bullets - integrate the facts into the prose.\n"
    "- When the TOOL OUTPUT mentions LangGraph nodes / classify / "
    "memory_recall / availability, you can briefly note that the workflow "
    "routed to that path - in plain language, not jargon.\n"
    "- Use markdown formatting: bullet lists for comparisons (one item per "
    "line, hyphen + space), **bold** for key numbers and product names, and "
    "a final 1-line follow-up suggestion when natural.\n"
    "- DO NOT use emojis or pictographs of any kind. No flags, weather "
    "symbols, food, transportation, or decorative icons. The product is "
    "premium and minimalist; emojis read as unprofessional.\n"
    "- Never mention 'tool output', 'system prompt', 'MCP', 'phase', or "
    "internal mode names explicitly - speak as the concierge, not the engine.\n"
)


# Model fallback chain. The live primary is Sonnet 5 (set in .env /
# config.bedrock.model_id) and _candidate_models() always tries that first.
# This chain is the BACKUP order if the primary errors: stay fast — Sonnet
# 5, then Haiku 4.5 — and keep Opus 4.8 last so a transient Sonnet hiccup
# on stage never silently falls back to the slowest model. First success wins.
_DEFAULT_FALLBACK_CHAIN: List[str] = [
    "global.anthropic.claude-sonnet-5",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "global.anthropic.claude-opus-4-8",
]


def _candidate_models() -> List[str]:
    primary = config.bedrock.model_id
    chain: List[str] = [primary]
    for m in _DEFAULT_FALLBACK_CHAIN:
        if m not in chain:
            chain.append(m)
    return chain


@dataclass
class PolishResult:
    """Outcome of a polish attempt.

    `text` always carries something the caller can show the user - the
    polished reply on success, or the original `tool_output` (with a
    `note` describing why polishing failed) when every model in the
    fallback chain errored out.
    """

    text: str
    model_id: Optional[str]  # which model actually answered
    note: Optional[str]  # error string if polish failed entirely


_bedrock_client = None


def _client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            config=Config(
                read_timeout=25,
                connect_timeout=5,
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        )
    return _bedrock_client


def _polish_sync(user_query: str, tool_output: str) -> PolishResult:
    """Synchronous Bedrock Converse call with model fallback chain.

    Tries each model in `_candidate_models()` until one succeeds. Returns
    a PolishResult that always has usable `text` (polished or raw) and
    metadata describing what happened.
    """
    if not tool_output:
        return PolishResult(text=tool_output, model_id=None, note="empty tool output")

    last_error: Optional[str] = None
    for model_id in _candidate_models():
        try:
            resp = _client().converse(
                modelId=model_id,
                system=[{"text": _CONCIERGE_SYSTEM}],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": (
                                    f"Traveler asked: {user_query}\n\n"
                                    f"TOOL OUTPUT (facts to use verbatim):\n"
                                    f"{tool_output}\n\nReply now."
                                )
                            }
                        ],
                    }
                ],
                inferenceConfig={
                    # Just maxTokens. Opus 4.8 deprecates `temperature`
                    # ("temperature is deprecated for this model"), and
                    # Haiku 4.5 rejects `temperature + topP` together.
                    # Letting the model pick its own sampling defaults
                    # is the only setting compatible across the entire
                    # Opus / Sonnet / Haiku fallback chain.
                    "maxTokens": 600,
                },
            )
            blocks = resp.get("output", {}).get("message", {}).get("content", [])
            for b in blocks:
                text = b.get("text")
                if text:
                    polished = text.strip()
                    logger.info(
                        "[polish] %s ok len=%d preview=%r",
                        model_id,
                        len(polished),
                        polished[:200],
                    )
                    return PolishResult(
                        text=polished,
                        model_id=model_id,
                        note=None,
                    )
            last_error = f"{model_id} returned an empty response"
        except Exception as exc:
            last_error = f"{model_id}: {exc.__class__.__name__}: {exc}"
            logger.warning("polish failed on %s: %s", model_id, exc)

    return PolishResult(
        text=tool_output,
        model_id=None,
        note=last_error or "every model in the fallback chain failed",
    )


async def polish_concierge_reply(user_query: str, tool_output: str) -> PolishResult:
    """Async wrapper around the Bedrock Converse call.

    Always returns a non-empty PolishResult: polished text on success,
    raw tool_output on failure with a `note` explaining why.
    """
    if not tool_output:
        return PolishResult(text=tool_output, model_id=None, note="empty tool output")
    return await asyncio.to_thread(_polish_sync, user_query, tool_output)
