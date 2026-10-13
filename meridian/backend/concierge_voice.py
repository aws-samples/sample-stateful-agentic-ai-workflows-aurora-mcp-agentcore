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

# The copy rules allow no em dash, en dash or middle dot, so replies use plain punctuation.
_MARK = "[\u2013\u2014\u00b7]"
_PUNCTUATION = (
    # A number range keeps a hyphen: Oct 12-19.
    (re.compile(r"(?<=\d)[ \t]*[\u2013\u2014][ \t]*(?=\d)"), "-"),
    # An en dash joining two words is a hyphen: Sydney-Melbourne.
    (re.compile(r"(?<=\w)\u2013(?=\w)"), "-"),
    # A mark that opens a line is a list marker.
    (re.compile(rf"(?m)^([ \t]*){_MARK}"), r"\1-"),
    # After punctuation it only separates, so it goes.
    (re.compile(rf"(?<=[,;:.!?])[ \t]*{_MARK}[ \t]*(?=\S)"), " "),
    # Before a line break it introduces what follows.
    (re.compile(rf"[ \t]*{_MARK}[ \t]*(?=\n)"), ":"),
    # Anywhere else it joins two clauses.
    (re.compile(rf"[ \t]*{_MARK}[ \t]*(?=\S)"), ", "),
)
_TRAILING_MARK = re.compile(rf"[ \t]*{_MARK}[ \t]*$")
# What a stream holds back: trailing spaces and a mark whose meaning the next text decides.
_PENDING = re.compile(rf"[ \t]*{_MARK}?[ \t]*$")


def _without_openers(text: str) -> str:
    prior = None
    while prior != text:
        prior, text = text, _PREFIX.sub(r"\1", text).lstrip()
    return text


def _plain_punctuation(text: str, *, final: bool) -> str:
    for pattern, replacement in _PUNCTUATION:
        text = pattern.sub(replacement, text)
    return _TRAILING_MARK.sub("", text) if final else text


def direct_reply(text: str) -> str:
    """Remove punctuated stock lead-ins and use plain punctuation, preserving facts."""
    return _plain_punctuation(_without_openers(text), final=True)


class DirectReplyStream:
    """Hold back only what the next text decides: a possible lead-in, so 'Gr' never flashes
    before 'Great!' is removed, and trailing spaces or a dash, so '12' then '-19' never shows a
    dash that a later character would have changed."""

    def __init__(self):
        self.raw = ""
        self.visible = ""

    def feed(self, text: str, *, final: bool = False) -> str:
        self.raw += text
        clean = _plain_punctuation(_without_openers(self.raw), final=final)
        if not final:
            start = list(_START.finditer(clean))[-1].end()
            tail = clean[start:].strip().strip("*_").casefold()
            if (tail and any(word.startswith(tail) for word in _OPENERS)) or clean[start:].strip() in {"*", "**", "_", "__"}:
                clean = clean[:start]
            clean = clean[:_PENDING.search(clean).start()]
        delta = clean[len(self.visible):]
        self.visible = clean
        return delta
