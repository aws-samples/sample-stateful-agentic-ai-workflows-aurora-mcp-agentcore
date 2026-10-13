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


def _assert_stream_matches(raw, expected):
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


# Replies follow the copy rules: no em dash, en dash or middle dot reaches the traveler.
@pytest.mark.parametrize(("raw", "expected"), [
    ("Your Tokyo culture trip is targeted for October 12\u201319.",
     "Your Tokyo culture trip is targeted for October 12-19."),
    ("Dates: Oct 12 \u2013 19, two adults.", "Dates: Oct 12-19, two adults."),
    ("It matches your boutique preference\u2014it is built around walks.",
     "It matches your boutique preference, it is built around walks."),
    ("Yanaka and Shimokitazawa \u2014 plus a rail pass.",
     "Yanaka and Shimokitazawa, plus a rail pass."),
    ("The Sydney\u2013Melbourne leg is direct.", "The Sydney-Melbourne leg is direct."),
    ("Two options:\n\u2013 Tokyo Walk\n\u2014 Kyoto Ryokan",
     "Two options:\n- Tokyo Walk\n- Kyoto Ryokan"),
    ("Booked. \u2014 Next, check the dates.", "Booked. Next, check the dates."),
    ("Here are your choices \u2014\n1. Tokyo", "Here are your choices:\n1. Tokyo"),
    ("JFK \u00b7 2 adults \u00b7 $3,200", "JFK, 2 adults, $3,200"),
    ("Great \u2014 Tokyo fits.", "Tokyo fits."),
    ("A plain hyphen - and well-known terms stay.", "A plain hyphen - and well-known terms stay."),
    ("The plan holds \u2014", "The plan holds"),
])
def test_dashes_and_middle_dots_become_plain_punctuation_at_every_token_boundary(raw, expected):
    _assert_stream_matches(raw, expected)


def test_tone_guard_never_releases_a_partial_stock_opener():
    stream = DirectReplyStream()
    assert stream.feed("Gr") == ""
    assert stream.feed("eat results") == ""
    assert stream.feed("! ") == ""
    assert stream.feed("Tokyo fits your saved preference.") == "Tokyo fits your saved preference."
