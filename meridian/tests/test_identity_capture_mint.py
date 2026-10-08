"""The token pipe refuses a terminal, writes one JSON object to a pipe and redacts its errors."""

import io
import json

from scripts.identity_capture import mint_session


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def fake_mint(user: str) -> dict:
    return {"access": f"access-{user}", "id": f"id-{user}"}


def test_it_refuses_a_terminal_and_signs_nobody_in():
    errors = io.StringIO()

    def mint(user):
        raise AssertionError("a terminal must not cause a sign-in")

    assert mint_session.main(mint=mint, stdout=Terminal(), stderr=errors) == 3
    assert "pipe" in errors.getvalue()


def test_it_writes_both_users_as_one_json_object_to_a_pipe():
    out = io.StringIO()

    code = mint_session.main(mint=fake_mint, stdout=out, stderr=io.StringIO())

    assert code == 0
    assert json.loads(out.getvalue()) == {
        "jordan": {"access": "access-jordan", "id": "id-jordan"},
        "decoy": {"access": "access-decoy", "id": "id-decoy"},
    }


def test_a_failure_is_one_redacted_line_and_nothing_on_standard_output():
    out, errors = io.StringIO(), io.StringIO()

    def mint(user):
        raise RuntimeError("cannot read the secret in 123456789012")

    assert mint_session.main(mint=mint, stdout=out, stderr=errors) == 1
    assert out.getvalue() == ""
    assert "<acct>" in errors.getvalue() and "123456789012" not in errors.getvalue()
