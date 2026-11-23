"""Which Data API failures call_with_retry repeats: only ones where the call never reached the service.

A fresh AgentCore microVM once lost a TLS handshake to the Data API (SSL EOF) on a read in the
availability step, and the whole recovery failed although the call had never been sent.
"""
import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
    SSLError,
)

from backend.db import rds_data_client
from backend.db.rds_data_client import call_with_retry, is_retryable

URL = "https://rds-data.us-east-1.amazonaws.com/Execute"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(rds_data_client.time, "sleep", lambda _seconds: None)


def failing(*errors):
    """A Data API method that raises each error in turn, then answers."""
    calls = []

    def method(**kwargs):
        calls.append(kwargs)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return {"records": []}
    return method, calls


NOT_SENT = [
    SSLError(endpoint_url=URL, error="EOF occurred in violation of protocol (_ssl.c:1038)"),
    ConnectTimeoutError(endpoint_url=URL),
    EndpointConnectionError(endpoint_url=URL),
]


@pytest.mark.parametrize("error", NOT_SENT, ids=lambda e: type(e).__name__)
def test_a_call_that_never_reached_the_service_is_retried(error):
    method, calls = failing(error)
    assert call_with_retry(method, sql="SELECT 1") == {"records": []}
    assert len(calls) == 2
    assert is_retryable(error)


MAY_HAVE_RUN = [
    ConnectionClosedError(endpoint_url=URL),
    ReadTimeoutError(endpoint_url=URL),
]


@pytest.mark.parametrize("error", MAY_HAVE_RUN, ids=lambda e: type(e).__name__)
def test_a_call_that_may_have_run_is_not_repeated(error):
    """Some calls outside a transaction are writes, such as an audit row: never send them twice."""
    method, calls = failing(error)
    with pytest.raises(type(error)):
        call_with_retry(method, sql="INSERT ...")
    assert len(calls) == 1
    assert not is_retryable(error)


def test_a_refused_statement_is_not_retried():
    error = ClientError({"Error": {"Code": "BadRequestException", "Message": "syntax"}}, "Execute")
    method, calls = failing(error)
    with pytest.raises(ClientError):
        call_with_retry(method, sql="SELEC 1")
    assert len(calls) == 1


def test_a_tls_drop_that_keeps_happening_gives_up_after_the_backoff():
    errors = [SSLError(endpoint_url=URL, error="EOF")] * 10
    method, calls = failing(*errors)
    with pytest.raises(SSLError):
        call_with_retry(method, sql="SELECT 1")
    assert len(calls) == len(rds_data_client.DATA_API_RETRY_BACKOFF_SECONDS) + 1
