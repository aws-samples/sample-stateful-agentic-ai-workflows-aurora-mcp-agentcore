"""An unhandled error returns a generic body and logs no exception text or credentials."""

from __future__ import annotations

import base64
import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.main import general_exception_handler

REFERENCE = re.compile(r"^[0-9a-f]{8}$")
OPAQUE = "opaque-bearer-" + "x" * 24


def _segment(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _jwt_shaped() -> str:
    header = _segment('{"alg":"RS256","kid":"abc"}')
    claims = _segment('{"sub":"u-1","token_use":"access"}')
    return f"{header}.{claims}.{_segment('signature-bytes')}"


@pytest.fixture
def client():
    app = FastAPI()
    app.add_exception_handler(Exception, general_exception_handler)

    @app.get("/boom")
    async def boom():
        raise RuntimeError(
            f"upstream refused: Authorization: Bearer {OPAQUE} body={_jwt_shaped()}"
        )

    return TestClient(app, raise_server_exceptions=False)


def test_the_body_is_generic_with_a_short_reference(client):
    response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "Internal server error"
    assert REFERENCE.match(body["request_id"])
    assert set(body) == {"error", "request_id"}
    assert "upstream" not in response.text


def test_neither_the_response_nor_the_log_carries_the_token_text(client, caplog, capsys):
    with caplog.at_level(logging.DEBUG):
        response = client.get("/boom")
    printed = capsys.readouterr()
    everything = response.text + caplog.text + printed.out + printed.err
    assert _jwt_shaped() not in everything
    assert OPAQUE not in everything
    assert "upstream refused" not in everything


def test_the_log_names_the_exception_class_and_the_reference(client, caplog):
    with caplog.at_level(logging.ERROR):
        response = client.get("/boom")
    ref = response.json()["request_id"]
    lines = [r.getMessage() for r in caplog.records if ref in r.getMessage()]
    assert lines and "RuntimeError" in lines[0]
