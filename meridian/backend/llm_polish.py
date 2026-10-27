"""
Concierge-tone polish over deterministic tool outputs.

The MCP path produces precise but dry readouts ("FX via meridian-concierge
MCP: 2500 USD ≈ 2300 EUR"). The app wants longer, narrative replies
that read like a real travel concierge. This module wraps a Bedrock
Converse call (Claude Sonnet 5.5 by default) around the deterministic facts so the user
sees a richer answer without the agent hallucinating numbers - all factual content comes
from the tool result that we feed verbatim into the system prompt.

One model answers: the configured one. Transient errors are retried by botocore with
adaptive retries. A persistent failure is reported with the model id and what to check,
and the raw facts are returned unpolished. A different model never answers in its place.

Returns a `PolishResult` carrying both the text AND metadata about which
model responded (or which error blocked the polish), so the caller can
log the model id into the trace and surface failures to the user.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError

from backend.bedrock_model import request_fields
from backend.config import config
from backend.security.redaction import mask_account_ids
from backend.timing import clock, elapsed_ms

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
    "- The traveler's request describes wishes, not verified package facts. "
    "Do not turn 'villa stay' into a private or exclusive villa. If a requested "
    "attribute is absent from the catalog, explicitly say it is unverified. "
    "Food experiences do not establish dietary suitability, and loyalty status "
    "does not establish a partner benefit. A search result is not a hold or "
    "booking; offer to review an option, not to lock in unverified terms.\n"
    "- Keep the response between 3 and 5 sentences (about 60-140 words).\n"
    "- Open with the direct answer to the traveler's question, not preamble.\n"
    "- When the TOOL OUTPUT lists ranked search results, the first product is "
    "the best match. Lead with it and do not promote a lower-ranked trip above "
    "it. For an explicit side-by-side comparison, however, the listed rows are "
    "comparison subjects rather than a preference ranking: summarize their "
    "tradeoffs and do not call one the leader unless the tool output explicitly "
    "does so. For ranked results, briefly "
    "explain the top pick's fit using the structured fields (price, trip_type, "
    "description, durations, and highlights), then end with a focused follow-up "
    "question. Reranker and vector scores are internal ordering signals, not "
    "calibrated confidence: never quote them as percentages or call them a "
    "'match percentage'. Use the ranked order instead.\n"
    "- An `availability` field represents trip-package inventory by "
    "duration. It is not airline seat inventory. Never claim that flights, "
    "seats, routes, or nonstop service were checked unless those exact facts "
    "appear in the tool output. Ask about choosing or holding a duration, not "
    "checking seats.\n"
    "- When the TOOL OUTPUT lists 'Traveler preferences applied to this turn', "
    "weave 3-5 of them naturally into the reply, prioritizing whichever "
    "preferences actually shape THIS recommendation. Examples: 'your "
    "no_red_eye rule out of JFK', 'within the boutique-over-chain lodging "
    "style you favor', 'I noted your shellfish allergy on dining picks', "
    "'we'll apply your Hotel Platinum / Airline Premier benefits where useful', "
    "'kept the Tokyo Oct 12-19 thread alive', 'sized for a 2-traveler party'. "
    "Vary which facts you cite turn-to-turn so the relationship feels "
    "lived-in - never repeat the same 1-2 facts every time. If the "
    "traveler is explicitly asking about prior turns or what was discussed, "
    "you may also briefly cite recent recalled trips. Do NOT enumerate "
    "the full list as bullets - integrate the facts into the prose.\n"
    "- When the TOOL OUTPUT mentions LangGraph nodes / classify / "
    "memory_recall / availability, you can briefly note that the workflow "
    "routed to that path - in plain language, not jargon.\n"
    "- Use markdown formatting: bullet lists only when the traveler explicitly "
    "asked for a comparison, **bold** for key numbers and product names, and "
    "a final 1-line follow-up suggestion when natural. When product cards are "
    "present, do not repeat every field from every card in prose.\n"
    "- DO NOT use emojis or pictographs of any kind. No flags, weather "
    "symbols, food, transportation, or decorative icons. The product is "
    "premium and minimalist; emojis read as unprofessional.\n"
    "- Never mention 'tool output', 'system prompt', 'MCP', 'phase', or "
    "internal mode names explicitly - speak as the concierge, not the engine.\n"
)


READ_TIMEOUT_SECONDS = 25
CONNECT_TIMEOUT_SECONDS = 5
MAX_ATTEMPTS = 3

# The output budget is the factory default: Sonnet 5.5 thinks adaptively when no thinking
# field is sent, and thinking tokens count against it.
POLISH_MAX_TOKENS = 16000


@dataclass
class PolishResult:
    """Outcome of a polish attempt.

    `text` always carries something the caller can show the user: the polished reply on
    success, or the original `tool_output` when the configured model could not answer,
    with a `note` saying why.
    """

    text: str
    model_id: Optional[str]  # the model that answered; None when polish failed
    note: Optional[str]  # why polish failed
    # How long the Converse call took.
    elapsed_ms: Optional[int] = None
    # True when no model was called, so nothing failed: there was nothing to polish.
    skipped: bool = False


_bedrock_client = None


def _client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            config=Config(
                read_timeout=READ_TIMEOUT_SECONDS,
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
                retries={"total_max_attempts": MAX_ATTEMPTS, "mode": "adaptive"},
            ),
        )
    return _bedrock_client


def _request(user_query: str, tool_output: str, model_id: str) -> dict:
    """The Converse request. No temperature: Sonnet 5.5 rejects one."""
    request = {
        "modelId": model_id,
        "system": [{"text": _CONCIERGE_SYSTEM}],
        "messages": [
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
        "inferenceConfig": {"maxTokens": POLISH_MAX_TOKENS},
    }
    fields = request_fields()
    if fields:
        request["additionalModelRequestFields"] = fields
    return request


_BUDGET_ADVICE = (
    f"Check the output budget, POLISH_MAX_TOKENS ({POLISH_MAX_TOKENS} tokens): output tokens, "
    "including any thinking, count against it."
)
_GUARDRAIL_ADVICE = "A content filter or guardrail stopped it."
_CONTEXT_ADVICE = "It reached the model's context window; retrying the same input will not help."
_ACCESS_ADVICE = (
    "Check that this account has access to the model and that the role allows "
    "bedrock:InvokeModel on the model or its inference profile."
)
_THROTTLE_ADVICE = "Try again shortly."
# Advice only where the stop reason or error code settles the cause; every other case states
# the facts and nothing more.
_STOP_ADVICE = {
    "max_tokens": _BUDGET_ADVICE,
    "content_filtered": _GUARDRAIL_ADVICE,
    "guardrail_intervened": _GUARDRAIL_ADVICE,
    "model_context_window_exceeded": _CONTEXT_ADVICE,
}
_CODE_ADVICE = {
    "AccessDeniedException": _ACCESS_ADVICE,
    "ThrottlingException": _THROTTLE_ADVICE,
}
_MESSAGE_LIMIT = 200


def _unavailable(tool_output: str, statement: str, advice: str = "") -> PolishResult:
    """Fail closed: the raw facts, a note of what happened, and advice only when certain."""
    advice_part = f" {advice}" if advice else ""
    stop = "" if statement.endswith("...") else "."
    note = f"The polish was not applied. {statement}{stop}{advice_part} No other model was tried."
    logger.warning(note)
    return PolishResult(text=tool_output, model_id=None, note=note)


def _first_text(response: dict) -> Optional[str]:
    blocks = response.get("output", {}).get("message", {}).get("content", [])
    for block in blocks:
        if block.get("text"):
            return block["text"].strip()
    return None


def _short(message: str) -> str:
    """The masked AWS message: cut at a word boundary with "..." when long, else without a
    trailing period, question mark, exclamation mark or colon."""
    message = mask_account_ids(message).strip()
    if len(message) > _MESSAGE_LIMIT:
        words = message[:_MESSAGE_LIMIT].rsplit(" ", 1)[0]
        return words.rstrip(" .,;:?!") + "..."
    return message.rstrip(" .?!:")


def _labelled(label: str, message: str) -> str:
    """The label and the short AWS message; no colon for an empty message."""
    message = _short(message)
    return f"{label}: {message}" if message else label


def _timeout_advice(seconds: int) -> str:
    return (
        f"The last attempt timed out after {seconds} seconds; the client makes up to "
        f"{MAX_ATTEMPTS} attempts in all. Try again."
    )


def _call_failure(exc: Exception) -> tuple[str, str]:
    """What happened and the advice (often empty) for an exception from the model call."""
    name = exc.__class__.__name__
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = error.get("Code", "")
        return _labelled(code or name, str(error.get("Message", ""))), _CODE_ADVICE.get(code, "")
    what = _labelled(name, str(exc))
    if isinstance(exc, ReadTimeoutError):
        return what, _timeout_advice(READ_TIMEOUT_SECONDS)
    if isinstance(exc, ConnectTimeoutError):
        return what, _timeout_advice(CONNECT_TIMEOUT_SECONDS)
    return what, ""


def _unusable(response: dict, polished: Optional[str]) -> Optional[tuple[str, str]]:
    """What happened and the advice when the response is not a finished reply, else None.

    The stop reason is judged first and reported exactly as returned, even when the response
    carries no text.
    """
    stop_reason = str(response.get("stopReason") or "")
    if not stop_reason:
        return "it returned no stop reason", ""
    if stop_reason != "end_turn":
        return f"it stopped with stop reason {stop_reason}", _STOP_ADVICE.get(stop_reason, "")
    if polished is None:
        return "it returned no text", ""
    visible_ending = polished.rstrip("*_` ").rstrip()
    if not visible_ending or visible_ending[-1] not in ".!?":
        return "its reply looked cut off", ""
    return None


def _polish_sync(user_query: str, tool_output: str) -> PolishResult:
    """Synchronous Bedrock Converse call to the configured model.

    Returns a PolishResult that always has usable `text` (polished or raw) and metadata
    describing what happened.
    """
    if not tool_output:
        return PolishResult(
            text=tool_output, model_id=None, note="empty tool output", skipped=True
        )

    model_id = config.bedrock.model_id
    started = clock()
    try:
        response = _client().converse(**_request(user_query, tool_output, model_id))
    except Exception as exc:
        what, advice = _call_failure(exc)
        return _unavailable(tool_output, f"{model_id} could not be called: {what}", advice)
    call_ms = elapsed_ms(started)

    polished = _first_text(response)
    problem = _unusable(response, polished)
    if problem:
        what, advice = problem
        return _unavailable(tool_output, f"{model_id} replied, but {what}", advice)
    logger.info("[polish] %s ok len=%d preview=%r", model_id, len(polished), polished[:200])
    return PolishResult(text=polished, model_id=model_id, note=None, elapsed_ms=call_ms)


async def polish_concierge_reply(user_query: str, tool_output: str) -> PolishResult:
    """Async wrapper around the Bedrock Converse call.

    Always returns a non-empty PolishResult: polished text on success,
    raw tool_output on failure with a `note` explaining why.
    """
    if not tool_output:
        return PolishResult(
            text=tool_output, model_id=None, note="empty tool output", skipped=True
        )
    return await asyncio.to_thread(_polish_sync, user_query, tool_output)
