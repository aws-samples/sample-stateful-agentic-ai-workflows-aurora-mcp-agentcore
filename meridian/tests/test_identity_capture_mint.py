"""Tokens leave the minter only through an inherited pipe, and nothing else ever sees them."""

import json
import logging
import os
import pty
import stat

import pytest

from scripts.identity_capture import mint_session

SENTINEL = "SENTINEL-TOKEN-NOT-A-REAL-JWT"


class Mint:
    """A stand-in for ``mint_tokens`` that records its calls and returns sentinel tokens."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, user: str) -> dict:
        self.calls.append(user)
        return {"access": f"{SENTINEL}-access-{user}", "id": f"{SENTINEL}-id-{user}"}


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("MERIDIAN_COGNITO_USER_POOL_ID", "pool-placeholder")
    monkeypatch.setenv("MERIDIAN_COGNITO_APP_CLIENT_ID", "client-placeholder")


@pytest.fixture
def pipe():
    read_end, write_end = os.pipe()
    yield read_end, write_end
    for fd in (read_end, write_end):
        try:
            os.close(fd)
        except OSError:
            pass


def assert_no_token_anywhere(capsys, caplog) -> None:
    seen = capsys.readouterr()
    assert SENTINEL not in seen.out
    assert SENTINEL not in seen.err
    assert SENTINEL not in caplog.text


def test_without_token_fd_it_exits_3_and_mints_nothing(capsys, caplog):
    mint = Mint()

    assert mint_session.main([], mint=mint) == 3

    assert mint.calls == []
    assert "--token-fd" in capsys.readouterr().err
    assert_no_token_anywhere(capsys, caplog)


def test_the_message_arrives_on_the_pipe_and_only_there(pipe, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    read_end, write_end = pipe

    code = mint_session.main(["--token-fd", str(write_end)], mint=Mint())

    assert code == 0
    message = json.loads(os.read(read_end, 65536))
    assert message["jordan"] == {
        "access": f"{SENTINEL}-access-jordan", "id": f"{SENTINEL}-id-jordan"}
    assert set(message) == {"jordan", "decoy"}
    seen = capsys.readouterr()
    assert (seen.out, seen.err) == ("", "")
    assert SENTINEL not in caplog.text


def test_it_closes_its_end_so_the_reader_sees_end_of_file(pipe):
    read_end, write_end = pipe
    mint_session.main(["--token-fd", str(write_end)], mint=Mint())
    os.read(read_end, 65536)

    assert os.read(read_end, 1) == b""


@pytest.mark.parametrize("fd", [0, 1, 2])
def test_it_refuses_the_standard_streams(fd, capsys, caplog):
    mint = Mint()

    assert mint_session.main(["--token-fd", str(fd)], mint=mint) == 3

    assert mint.calls == []
    assert_no_token_anywhere(capsys, caplog)


def test_it_refuses_a_terminal(capsys, caplog):
    master, slave = pty.openpty()
    mint = Mint()
    try:
        assert mint_session.main(["--token-fd", str(slave)], mint=mint) == 3
    finally:
        os.close(master)
        os.close(slave)

    assert mint.calls == []
    assert "terminal" in capsys.readouterr().err


def test_it_refuses_a_regular_file(tmp_path, capsys, caplog):
    mint = Mint()
    with open(tmp_path / "sink", "wb") as sink:
        assert mint_session.main(["--token-fd", str(sink.fileno())], mint=mint) == 3

    assert mint.calls == []
    assert "pipe" in capsys.readouterr().err
    assert (tmp_path / "sink").read_bytes() == b""


def test_it_refuses_a_descriptor_that_is_not_open(capsys):
    mint = Mint()

    assert mint_session.main(["--token-fd", "977"], mint=mint) == 3

    assert mint.calls == []
    assert "not open" in capsys.readouterr().err


def test_it_refuses_a_value_that_is_not_a_number(capsys):
    assert mint_session.main(["--token-fd", "pipe"], mint=Mint()) == 3
    assert "int" in capsys.readouterr().err


def test_check_validates_the_plumbing_and_never_mints(pipe, capsys, caplog):
    read_end, write_end = pipe
    mint = Mint()

    code = mint_session.main(["--check", "--token-fd", str(write_end)], mint=mint)

    assert code == 0
    assert mint.calls == []
    assert stat.S_ISFIFO(os.fstat(write_end).st_mode)
    os.set_blocking(read_end, False)
    with pytest.raises(BlockingIOError):
        os.read(read_end, 1)
    assert_no_token_anywhere(capsys, caplog)


def test_check_still_refuses_a_bad_descriptor_and_missing_settings(pipe, monkeypatch, capsys):
    mint = Mint()
    assert mint_session.main(["--check", "--token-fd", "1"], mint=mint) == 3
    assert mint_session.main(["--check"], mint=mint) == 3

    monkeypatch.delenv("MERIDIAN_COGNITO_APP_CLIENT_ID")
    assert mint_session.main(["--check", "--token-fd", str(pipe[1])], mint=mint) == 1
    assert "MERIDIAN_COGNITO_APP_CLIENT_ID" in capsys.readouterr().err
    assert mint.calls == []


def test_a_failure_is_one_redacted_line_and_nothing_on_the_pipe(pipe, capsys, caplog):
    read_end, write_end = pipe

    def mint(user):
        raise RuntimeError("cannot read the secret in 123456789012")

    assert mint_session.main(["--token-fd", str(write_end)], mint=mint) == 1

    err = capsys.readouterr().err
    assert "<acct>" in err and "123456789012" not in err
    assert os.read(read_end, 10) == b""


def test_a_second_user_failing_sends_no_partial_message(pipe):
    read_end, write_end = pipe

    def mint(user):
        if user == "decoy":
            raise RuntimeError("sign-in failed")
        return {"access": SENTINEL, "id": SENTINEL}

    assert mint_session.main(["--token-fd", str(write_end)], mint=mint) == 1
    assert os.read(read_end, 10) == b""


def test_a_closed_reader_is_a_clean_failure_that_names_no_token(pipe, capsys, caplog):
    read_end, write_end = pipe
    os.close(read_end)

    assert mint_session.main(["--token-fd", str(write_end)], mint=Mint()) == 1
    assert_no_token_anywhere(capsys, caplog)


@pytest.mark.parametrize("fd", [0, 1, 2])
def test_it_refuses_a_standard_stream_even_when_it_is_a_pipe(fd, pipe):
    """An agent shell's standard streams are pipes, which is how the first design leaked."""
    read_end, write_end = pipe
    saved = os.dup(fd)
    os.dup2(write_end, fd)
    mint = Mint()
    try:
        code = mint_session.main(["--token-fd", str(fd)], mint=mint)
    finally:
        os.dup2(saved, fd)
        os.close(saved)

    assert code == 3
    assert mint.calls == []
    os.set_blocking(read_end, False)
    with pytest.raises(BlockingIOError):
        os.read(read_end, 1)


def test_help_prints_usage_and_mints_nothing(capsys, caplog):
    mint = Mint()

    with pytest.raises(SystemExit) as stop:
        mint_session.main(["--help"], mint=mint)

    assert stop.value.code == 0
    assert mint.calls == []
    assert "--token-fd" in capsys.readouterr().out
    assert_no_token_anywhere(capsys, caplog)
