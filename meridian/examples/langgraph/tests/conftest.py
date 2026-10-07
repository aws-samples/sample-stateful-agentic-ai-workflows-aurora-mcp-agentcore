"""Isolation for the LangGraph example tests.

The application suite's autouse fixture lives under ``tests/`` and does not
reach this package, so the parts these tests need are repeated here: unit
tests get placeholder credentials and any unmocked network connection fails.
Tests marked ``database`` keep the live configuration from the environment.
"""

import socket

import pytest
from dotenv import load_dotenv

load_dotenv()


@pytest.fixture(autouse=True)
def isolated_unit_environment(request, monkeypatch):
    """Keep unit tests off developer credentials and off the network."""
    if request.node.get_closest_marker("database"):
        yield
        return
    for name, value in {
        "AWS_ACCESS_KEY_ID": "unit-test",
        "AWS_SECRET_ACCESS_KEY": "unit-test",
        "AWS_EC2_METADATA_DISABLED": "true",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)

    attempted = []
    connect = socket.socket.connect

    def refuse_network(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            attempted.append(address)
            raise AssertionError("Unit test attempted an unmocked network connection")
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    yield
    assert not attempted, "Unit test attempted an unmocked network connection"
