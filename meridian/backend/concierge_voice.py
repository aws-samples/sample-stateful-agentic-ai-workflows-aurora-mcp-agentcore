"""Keep traveler-facing narration direct, including partial model tokens."""

import re

VOICE = (
    "Speak as a calm, concise travel concierge. Lead with the answer or a useful question. "
    "Do not open with Great, Perfect, Excellent, Absolutely, Wonderful, Fantastic, Amazing, "
    "Sure, Certainly, Of course, or similar applause. Avoid hype, flattery and unsupported "
    "superlatives. Do not narrate routine searches; the interface shows progress. "
    "Use remembered details naturally only when present in the authorized context. "
    "Do not claim a preference was saved unless a tool confirmed that write."
)

_OPENERS = (
    "great", "great results", "great match", "great choice", "great question", "great news",
    "perfect", "perfect choice", "excellent", "excellent choice", "excellent news",
    "absolutely", "wonderful", "fantastic", "amazing", "sure", "certainly", "of course",
    "happy to help", "good news", "good question", "sounds good", "sounds great",
    "that sounds great", "that sounds perfect",
)
_BOUNDARY = r"(^|(?<=[.!?])\s+|\n+)"
_PREFIX = re.compile(
    _BOUNDARY + r"(?:\*\*|__)?(?:" + "|".join(re.escape(word) for word in _OPENERS)
    + r")(?:\*\*|__)?[ \t]*(?:[!.,:;…]+|[—–-])(?:\*\*|__)?[ \t]*", re.IGNORECASE,
)
_START = re.compile(_BOUNDARY)


def direct_reply(text: str) -> str:
    """Remove punctuated stock lead-ins, preserving names, quotations and facts."""
    prior = None
    while prior != text:
        prior, text = text, _PREFIX.sub(r"\1", text).lstrip()
    return text


class DirectReplyStream:
    """Hold only a possible lead-in so 'Gr' never flashes before 'Great!' is removed."""

    def __init__(self):
        self.raw = ""
        self.visible = ""

    def feed(self, text: str, *, final: bool = False) -> str:
        self.raw += text
        clean = direct_reply(self.raw)
        if not final:
            start = list(_START.finditer(clean))[-1].end()
            tail = clean[start:].strip().strip("*_").casefold()
            if (tail and any(word.startswith(tail) for word in _OPENERS)) or clean[start:].strip() in {"*", "**", "_", "__"}:
                clean = clean[:start]
        delta = clean[len(self.visible):]
        self.visible = clean
        return delta
