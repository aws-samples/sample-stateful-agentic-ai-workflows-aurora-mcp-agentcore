"""
Concierge-tone polish over deterministic tool outputs.

The MCP path produces precise but dry readouts ("FX via meridian-concierge
MCP: 2500 USD ≈ 2300 EUR"). On stage we want longer, narrative replies
that read like a real travel concierge. This module wraps a Bedrock
Converse call (Claude Opus 4.7 by default, with fallback to Sonnet 4.5
and Haiku) around the deterministic facts so the user sees a richer
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
    "MCP servers into a warm, helpful, well-structured reply.\n\n"
    "Hard rules:\n"
    "- Use ONLY the facts in the TOOL OUTPUT. Do not invent prices, dates, "
    "destinations, points balances, or rates. If a number is in the output, "
    "reuse it verbatim. If something is unknown, say so honestly.\n"
    "- Keep the response between 3 and 6 sentences (about 70-150 words).\n"
    "- Open with the direct answer to the traveler's question, not preamble.\n"
    "- Use light markdown: bullet lists for comparisons, **bold** for key "
    "numbers, and a final 1-line follow-up suggestion when natural.\n"
    "- Never mention 'tool output', 'system prompt', or 'MCP' explicitly - "
    "speak as the concierge, not the engine.\n"
)


# Model fallback chain. Opus 4.7 is the headline; if the AWS account
# doesn't have access to that cross-region profile (very common in
# fresh accounts), we try Sonnet 4.6 and finally Haiku 4.5. The first
# success wins; if all three fail we surface the last error.
_DEFAULT_FALLBACK_CHAIN: List[str] = [
    "global.anthropic.claude-opus-4-7",
    "global.anthropic.claude-sonnet-4-6",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
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
                    "maxTokens": 600,
                    "temperature": 0.6,
                    "topP": 0.9,
                },
            )
            blocks = resp.get("output", {}).get("message", {}).get("content", [])
            for b in blocks:
                text = b.get("text")
                if text:
                    return PolishResult(
                        text=text.strip(),
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
