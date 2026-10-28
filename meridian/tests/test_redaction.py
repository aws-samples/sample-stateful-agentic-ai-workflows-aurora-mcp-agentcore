"""Account ids are masked at read time; the stored rows keep the ARN."""

import pytest
from hypothesis import given, strategies as st

from backend.security.redaction import (
    ACCOUNT_ID_PATTERN,
    mask_account_ids,
    mask_payload,
    mask_secrets,
)

ACCOUNT = "123456789012"
OTHER = "111122223333"


def test_a_bare_account_id_is_masked():
    assert mask_account_ids(ACCOUNT) == "<acct>"


def test_an_id_inside_an_arn_is_masked():
    arn = f"arn:aws:sts::{ACCOUNT}:assumed-role/Meridian/session-a"
    assert mask_account_ids(arn) == "arn:aws:sts::<acct>:assumed-role/Meridian/session-a"


def test_every_id_in_one_string_is_masked():
    assert mask_account_ids(f"{ACCOUNT} then {OTHER}") == "<acct> then <acct>"


@pytest.mark.parametrize("digits", ["12345678901", "1234567890123", "12345678901234567890"])
def test_runs_shorter_or_longer_than_twelve_digits_are_left_alone(digits):
    assert mask_account_ids(f"id={digits}") == f"id={digits}"


def test_masking_is_idempotent():
    once = mask_account_ids(f"arn:aws:iam::{ACCOUNT}:role/x")
    assert mask_account_ids(once) == once


def test_a_payload_is_masked_deeply_and_the_original_is_untouched():
    original = {
        "subject": f"arn:aws:sts::{ACCOUNT}:assumed-role/r/s",
        "fields": [{"label": "iam_identity", "value": f"arn:aws:iam::{OTHER}:role/x"}],
        "pair": (ACCOUNT, 7),
        "count": 12,
        "ok": True,
        "nothing": None,
        f"key-{ACCOUNT}": "v",
    }
    masked = mask_payload(original)

    assert masked["subject"] == "arn:aws:sts::<acct>:assumed-role/r/s"
    assert masked["fields"][0]["value"] == "arn:aws:iam::<acct>:role/x"
    assert masked["pair"] == ("<acct>", 7)
    assert isinstance(masked["pair"], tuple)
    assert (masked["count"], masked["ok"], masked["nothing"]) == (12, True, None)
    assert "key-<acct>" in masked
    assert original["subject"].startswith(f"arn:aws:sts::{ACCOUNT}")
    assert masked is not original and masked["fields"] is not original["fields"]


@given(st.from_regex(r"\d{12}", fullmatch=True), st.from_regex(r"[:/ .,\"']{0,8}", fullmatch=True))
def test_no_twelve_digit_run_survives(digits, padding):
    assert not ACCOUNT_ID_PATTERN.search(mask_account_ids(f"{padding}{digits}{padding}"))


def test_a_uuid_whose_last_group_is_twelve_digits_is_unchanged():
    uuid = "f29c6d37-e9b9-4e77-bba8-890123456789"
    assert mask_account_ids(uuid) == uuid
    assert mask_account_ids(f"span {uuid}.") == f"span {uuid}."
    upper = uuid.upper()
    assert mask_account_ids(f"/spans/{upper}/x") == f"/spans/{upper}/x"


@pytest.mark.parametrize("tail", ["abc", "-x", " text", "_tail", ".log", ", then more"])
def test_a_uuid_tail_followed_by_text_keeps_its_tail(tail):
    uuid = "f29c6d37-e9b9-4e77-bba8-890123456789"
    assert mask_account_ids(f"span {uuid}{tail}") == f"span {uuid}{tail}"


@pytest.mark.parametrize("arn", [
    f"arn:aws:iam::{ACCOUNT}:role/x",
    f"arn:aws:sts::{ACCOUNT}:assumed-role/x/y",
])
def test_arns_are_masked(arn):
    assert ACCOUNT not in mask_account_ids(arn)
    assert "::<acct>:" in mask_account_ids(arn)


@pytest.mark.parametrize(("text", "expected"), [
    (ACCOUNT, "<acct>"),
    (f"account {ACCOUNT}.", "account <acct>."),
    (f'"{ACCOUNT}"', '"<acct>"'),
    (f"{ACCOUNT} and {OTHER}", "<acct> and <acct>"),
    (f"a,{ACCOUNT}", "a,<acct>"),
    (f"id-{ACCOUNT}", "id-<acct>"),
    (f"cdk-hnb659fds-assets-{ACCOUNT}-us-east-1", "cdk-hnb659fds-assets-<acct>-us-east-1"),
    (f"/aws/x/fn-{ACCOUNT}", "/aws/x/fn-<acct>"),
])
def test_standalone_ids_are_masked_at_any_boundary(text, expected):
    assert mask_account_ids(text) == expected


@pytest.mark.parametrize("text", [f"abc{ACCOUNT}", f"{ACCOUNT}abc", f"{ACCOUNT}x", f"7{ACCOUNT}"])
def test_twelve_digits_attached_to_letters_or_digits_are_left_alone(text):
    assert mask_account_ids(text) == text


def test_the_account_id_in_a_botocore_request_url_is_masked():
    # botocore puts the request URL in a transport error, with the Runtime ARN percent-encoded.
    path = (f"/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A{ACCOUNT}%3Aruntime%2Frt-1"
            "/invocations?qualifier=DEFAULT")

    assert mask_account_ids(path) == (
        "/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A<acct>%3Aruntime%2Frt-1"
        "/invocations?qualifier=DEFAULT")


@pytest.mark.parametrize("separator", ["%3A", "%3a", "%2F", "%2f"])
def test_a_percent_encoded_colon_or_slash_bounds_an_id_on_either_side(separator):
    assert mask_account_ids(f"x{separator}{ACCOUNT}") == f"x{separator}<acct>"
    assert mask_account_ids(f"{ACCOUNT}{separator}x") == f"<acct>{separator}x"
    assert mask_account_ids(f"{separator}{ACCOUNT}{separator}") == f"{separator}<acct>{separator}"


@pytest.mark.parametrize("text", [f"3A{ACCOUNT}", f"2F{ACCOUNT}", f"%3A{ACCOUNT}x",
                                  f"%3A{ACCOUNT}7", f"%3A7{ACCOUNT}"])
def test_an_encoded_separator_does_not_free_an_id_a_letter_or_digit_touches(text):
    assert mask_account_ids(text) == text


def test_a_uuid_in_a_percent_encoded_path_is_unchanged():
    path = "%2Fspans%2Ff29c6d37-e9b9-4e77-bba8-890123456789%2F"
    assert mask_account_ids(path) == path


def _raised(message):
    def innermost():
        raise RuntimeError(message)

    try:
        innermost()
    except RuntimeError as error:
        return error


def test_a_traceback_keeps_its_frames_and_loses_the_account_id():
    from backend.security.redaction import masked_traceback

    text = masked_traceback(_raised(f"role arn:aws:iam::{ACCOUNT}:role/x denied"))

    assert "Traceback" in text and "in innermost" in text and "RuntimeError" in text
    assert ACCOUNT not in text and "arn:aws:iam::<acct>:role/x denied" in text


def test_a_long_traceback_keeps_its_start_and_its_end_within_the_limit():
    from backend.security.redaction import masked_traceback

    text = masked_traceback(_raised("x" * 500 + f" {ACCOUNT} tail"), limit=200)

    assert len(text) == 200 and text.startswith("Traceback") and "\n...\n" in text
    assert text.endswith("<acct> tail\n")


@pytest.mark.parametrize("text, masked", [
    ("password=hunter2", "password=<masked>"),
    ("login failed token=abc for x", "login failed token=<masked> for x"),
    ("SECRET=x", "SECRET=<masked>"),
    ("api_key=y", "api_key=<masked>"),
])
def test_a_credential_assignment_is_masked(text, masked):
    from backend.security.redaction import mask_credentials

    assert mask_credentials(text) == masked


def test_text_without_an_assignment_is_left_alone_and_masking_twice_changes_nothing():
    from backend.security.redaction import mask_credentials

    assert mask_credentials("the password was wrong") == "the password was wrong"
    once = mask_credentials("token=abc and key=def")
    assert mask_credentials(once) == once


def _outer_error():
    raise ValueError("outer")


def _chained(message, link):
    try:
        try:
            raise RuntimeError(message)
        except RuntimeError as inner:
            if link == "cause":
                raise ValueError("outer") from inner
            _outer_error()
    except ValueError as error:
        return error


@pytest.mark.parametrize("link", ["cause", "context"])
def test_a_chained_exception_is_masked_like_the_outer_one(link):
    from backend.security.redaction import masked_traceback

    text = masked_traceback(_chained(f"password=hunter2 for {ACCOUNT}", link))

    assert "RuntimeError" in text and "ValueError" in text
    assert "hunter2" not in text and ACCOUNT not in text
    assert "password=<masked>" in text and "<acct>" in text


def test_a_cut_never_leaves_half_a_credential():
    from backend.security.redaction import masked_traceback

    text = masked_traceback(_raised("x" * 500 + " password=hunter2"), limit=200)

    assert "hunter2" not in text and "hunter" not in text


def test_mask_secrets_masks_bearer_credentials_and_account_ids_without_cutting():
    text = "password=hunter2 arn:aws:iam::123456789012:role/x Bearer abc.def.ghi " + "w " * 300
    masked = mask_secrets(text)
    assert masked.startswith(
        "password=<masked> arn:aws:iam::<acct>:role/x Bearer <token> ")
    assert "hunter2" not in masked and "abc.def.ghi" not in masked and len(masked) > 400
    assert mask_secrets(masked) == masked


@pytest.mark.parametrize("text", [
    'refused Bearer "eyJabc.def.ghi" now',
    "refused Bearer 'abcdefghij' now",
    "Authorization: Bearer abcdefghij1234",
    "{'Authorization': 'Bearer abcdefghij1234'}",
])
def test_mask_secrets_masks_a_bearer_token_however_it_is_quoted(text):
    masked = mask_secrets(text)
    assert "Bearer <token>" in masked
    assert "abcdefghij" not in masked and "eyJabc" not in masked


def test_mask_secrets_leaves_the_fixed_no_bearer_token_text_alone():
    text = "No signed-in caller: no bearer token was forwarded."
    assert mask_secrets(text) == text


JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhYmMifQ.c2lnbmF0dXJl"


@pytest.mark.parametrize("text, masked", [
    (f"token invalid: {JWT}", "token invalid: <token>"),
    (f"access_token: {JWT}", "access_token: <masked>"),
    (f"authorization={JWT}", "authorization=<masked>"),
    (f"Bearer: {JWT}", "Bearer <token>"),
    (f'{{"token": "{JWT}"}}', '{"token": <masked>}'),
])
def test_mask_secrets_masks_the_measured_token_forms(text, masked):
    assert mask_secrets(text) == masked
    assert mask_secrets(masked) == masked


@pytest.mark.parametrize("name", ["password", "secret", "key", "authorization"])
def test_mask_secrets_masks_a_json_value_for_each_credential_name(name):
    assert mask_secrets(f'{{"{name}": "hunter2"}}') == f'{{"{name}": <masked>}}'


@pytest.mark.parametrize("text", [
    "Rejected a Cognito token: expired",
    "missing key: 'origin'",
    "monkey: banana",
    "token: expired for this traveler",
])
def test_mask_secrets_keeps_a_reason_or_an_ordinary_word_readable(text):
    assert mask_secrets(text) == text


def test_mask_secrets_masks_an_opaque_bearer_value_after_a_colon_or_authorization():
    assert mask_secrets("Authorization: Bearer abcdefghijkl") == "Authorization: Bearer <token>"
    assert mask_secrets("secret: abcdefghijklmnop1234") == "secret: <masked>"
