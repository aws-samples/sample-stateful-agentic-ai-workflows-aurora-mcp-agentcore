import pytest

from backend.concierge_voice import DirectReplyStream, direct_reply


@pytest.mark.parametrize(("raw", "expected"), [
    ("Great! Tokyo has three options.", "Tokyo has three options."),
    ("Great results! Found Tokyo.\n\nPerfect—here are the details.", "Found Tokyo.\n\nhere are the details."),
    ("Absolutely, I can check. Excellent! Two trips are available.", "I can check. Two trips are available."),
    ("Sure! Of course, here are the options.", "here are the options."),
    ("**Great!** Tokyo fits.\n\n__Perfect__! **Two** trips remain.", "Tokyo fits.\n\n**Two** trips remain."),
    ("Great Barrier Reef is available. Perfect Day Escape costs $500.", "Great Barrier Reef is available. Perfect Day Escape costs $500."),
    ('You called it "Perfect!". The price is $1,599.', 'You called it "Perfect!". The price is $1,599.'),
])
def test_voice_is_identical_at_every_token_boundary(raw, expected):
    assert direct_reply(raw) == expected
    for split in range(len(raw) + 1):
        stream = DirectReplyStream()
        first = stream.feed(raw[:split])
        second = stream.feed(raw[split:])
        final = stream.feed("", final=True)
        assert expected.startswith(first)
        assert expected.startswith(first + second)
        assert first + second + final == expected
    stream = DirectReplyStream()
    received = ""
    for char in raw:
        received += stream.feed(char)
        assert expected.startswith(received)
    assert received + stream.feed("", final=True) == expected


def test_tone_guard_never_releases_a_partial_stock_opener():
    stream = DirectReplyStream()
    assert stream.feed("Gr") == ""
    assert stream.feed("eat results") == ""
    assert stream.feed("! ") == ""
    assert stream.feed("Tokyo fits your saved preference.") == "Tokyo fits your saved preference."
