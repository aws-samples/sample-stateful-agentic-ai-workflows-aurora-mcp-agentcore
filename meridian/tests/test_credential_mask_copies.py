"""The four copies of the credential mask agree on every input, and say what they mask."""

import sys
from pathlib import Path

import pytest

from backend.security.redaction import mask_credentials
from tests.lambda_targets_support import load_target
from tests.test_holds_lambda import holds

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

import tool_errors  # noqa: E402

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhYmMifQ.c2lnbmF0dXJl"
COPIES = {
    "backend": mask_credentials,
    "holds": holds._mask_credentials,
    "catalog": load_target("meridian_catalog", "meridian_catalog_copy")._mask_credentials,
    "runtime": tool_errors._mask_credentials,
}

MASKED = [
    ("Authorization: Bearer abcdefghijkl", "Authorization: Bearer <token>"),
    ('{"authorization": "Bearer abcdefghijkl"}', '{"authorization": "Bearer <token>"}'),
    ("refused Bearer abcdefghijkl now", "refused Bearer <token> now"),
    ("Bearer: abcdefghijkl", "Bearer <token>"),
    ("refused Bearer 'abcdefghij' now", "refused Bearer <token>' now"),
    (f"token invalid: {JWT}", "token invalid: <token>"),
    (f"access_token: {JWT}", "access_token: <masked>"),
    (f"authorization={JWT}", "authorization=<masked>"),
    (f"Bearer: {JWT}", "Bearer <token>"),
    (f'{{"token": "{JWT}"}}', '{"token": <masked>}'),
    ('{"password": "hunter2"}', '{"password": <masked>}'),
    ('body \\"token\\": \\"abc\\" end', 'body \\"token\\": <masked> end'),
    ("password=hunter2 key=y", "password=<masked> key=<masked>"),
    ("api_key: abcdefghijklmnop1234", "api_key: <masked>"),
    ("accesskey=abc123", "accesskey=<masked>"),
    ("secretkey=abc", "secretkey=<masked>"),
    ("privatekey=abc", "privatekey=<masked>"),
    ("apikey=abc", "apikey=<masked>"),
    ("authkey=abc", "authkey=<masked>"),
    ("SigningKey=abc", "SigningKey=<masked>"),
    ('{"secretkey": "abc"}', '{"secretkey": <masked>}'),
]
READABLE = [
    "Rejected a Cognito token: expired",
    "missing key: 'origin'",
    "monkey: banana",
    "monkey=banana",
    "No signed-in caller: no bearer token was forwarded.",
    "duplicate key value violates unique constraint",
    "KeyError: 'x'",
    "token: two words here that are long enough to look secret",
]


@pytest.mark.parametrize("text, masked", MASKED)
@pytest.mark.parametrize("name", COPIES)
def test_each_copy_masks_the_secret_forms(name, text, masked):
    assert COPIES[name](text) == masked
    assert COPIES[name](masked) == masked


@pytest.mark.parametrize("text", READABLE)
@pytest.mark.parametrize("name", COPIES)
def test_each_copy_keeps_a_reason_or_an_ordinary_word_readable(name, text):
    assert COPIES[name](text) == text


@pytest.mark.parametrize("text", [text for text, _ in MASKED] + READABLE)
def test_all_four_copies_give_the_same_output(text):
    assert len({copy(text) for copy in COPIES.values()}) == 1
