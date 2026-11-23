"""Which Data API failures are sent again: only ones where the request never reached the service.

A fresh AgentCore microVM once lost a TLS handshake to the Data API (SSL EOF) on a read in the
availability step, and the whole recovery failed although the call had never been sent. Calls
outside a transaction also repeat throttling and 5xx answers; a statement inside a transaction
and a commit repeat only a request that never arrived. The errors below are built as botocore
raises them; tests/test_data_api_tls_drop.py produces the same chains from a real TLS server.
"""
import ssl

import pytest
import urllib3
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
    SSLError,
)

from backend.db import rds_data_client
from backend.db.rds_data_client import (
    RDSDataClient,
    call_unless_sent,
    call_with_retry,
    is_retryable,
    never_sent,
)

URL = "https://rds-data.us-east-1.amazonaws.com/Execute"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(rds_data_client.time, "sleep", lambda _seconds: None)


def tls_eof():
    """A TLS session that ended before the request went out: the handshake broke."""
    cause = ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation "
                               "of protocol (_ssl.c:1035)")
    return SSLError(endpoint_url=URL, error=urllib3.exceptions.SSLError(cause))


def tls_broken_record():
    """A broken TLS record in the reply: the request arrived and its statement may have run."""
    cause = ssl.SSLError(1, "[SSL: RECORD_LAYER_FAILURE] record layer failure (_ssl.c:2663)")
    return SSLError(endpoint_url=URL, error=urllib3.exceptions.SSLError(cause))


def throttled():
    return ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "Execute")


def failing(*errors):
    """A Data API method that raises each error in turn, then answers."""
    calls = []

    def method(**kwargs):
        calls.append(kwargs)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return {"records": []}
    return method, calls


NOT_SENT = [tls_eof, lambda: ConnectTimeoutError(endpoint_url=URL),
            lambda: EndpointConnectionError(endpoint_url=URL)]
MAY_HAVE_RUN = [lambda: ConnectionClosedError(endpoint_url=URL),
                lambda: ReadTimeoutError(endpoint_url=URL), tls_broken_record]


def _name(make):
    error = make()
    return f"{type(error).__name__}-{'eof' if never_sent(error) else 'sent'}"


@pytest.mark.parametrize("call", [call_with_retry, call_unless_sent])
@pytest.mark.parametrize("make", NOT_SENT, ids=_name)
def test_a_request_that_never_reached_the_service_is_sent_again(call, make):
    method, calls = failing(make())
    assert call(method, sql="SELECT 1") == {"records": []}
    assert len(calls) == 2
    assert never_sent(make()) and is_retryable(make())


@pytest.mark.parametrize("call", [call_with_retry, call_unless_sent])
@pytest.mark.parametrize("make", MAY_HAVE_RUN, ids=_name)
def test_a_request_that_may_have_run_is_not_sent_again(call, make):
    """Some calls outside a transaction are writes, such as an audit row: never send them twice."""
    error = make()
    method, calls = failing(error)
    with pytest.raises(type(error)):
        call(method, sql="INSERT ...")
    assert len(calls) == 1
    assert not never_sent(error) and not is_retryable(error)


def test_throttling_is_retried_outside_a_transaction_only():
    method, calls = failing(throttled())
    assert call_with_retry(method, sql="SELECT 1") == {"records": []}
    assert len(calls) == 2
    method, calls = failing(throttled())
    with pytest.raises(ClientError):
        call_unless_sent(method, sql="UPDATE ...")
    assert len(calls) == 1


def test_a_refused_statement_is_not_retried():
    error = ClientError({"Error": {"Code": "BadRequestException", "Message": "syntax"}}, "Execute")
    method, calls = failing(error)
    with pytest.raises(ClientError):
        call_with_retry(method, sql="SELEC 1")
    assert len(calls) == 1


def test_a_tls_drop_that_keeps_happening_gives_up_after_the_backoff():
    method, calls = failing(*(tls_eof() for _ in range(10)))
    with pytest.raises(SSLError):
        call_unless_sent(method, sql="SELECT 1")
    assert len(calls) == len(rds_data_client.DATA_API_RETRY_BACKOFF_SECONDS) + 1


class FakeBoto:
    """The boto rds-data methods the client calls, failing as told, then answering."""

    def __init__(self, execute_errors=(), commit_errors=()):
        self.execute_statement, self.executed = failing(*execute_errors)
        self.commit_transaction, self.commits = failing(*commit_errors)


def recorded_client(**errors):
    """The real execute and commit over a fake boto client: no AWS client is built."""
    client = RDSDataClient.__new__(RDSDataClient)
    client.cluster_arn, client.secret_arn, client.database = "cluster", "secret", "meridian"
    client.client = FakeBoto(**errors)
    return client


async def test_a_statement_inside_a_transaction_is_sent_again_only_if_it_never_arrived():
    client = recorded_client(execute_errors=[tls_eof()])
    assert await client.execute("SELECT 1", transaction_id="tx-1") == []
    assert [call["transactionId"] for call in client.client.executed] == ["tx-1", "tx-1"]

    client = recorded_client(execute_errors=[tls_broken_record()])
    with pytest.raises(SSLError):
        await client.execute("UPDATE journey_executions SET ...", transaction_id="tx-1")
    assert len(client.client.executed) == 1


async def test_a_statement_outside_a_transaction_keeps_the_wider_retry():
    client = recorded_client(execute_errors=[throttled()])
    assert await client.execute("SELECT 1") == []
    assert len(client.client.executed) == 2
    assert "transactionId" not in client.client.executed[0]


def test_a_commit_is_sent_again_only_if_it_never_arrived():
    client = recorded_client(commit_errors=[tls_eof()])
    client.commit_transaction("tx-1")
    assert [call["transactionId"] for call in client.client.commits] == ["tx-1", "tx-1"]

    client = recorded_client(commit_errors=[ConnectionClosedError(endpoint_url=URL)])
    with pytest.raises(ConnectionClosedError):
        client.commit_transaction("tx-1")
    assert len(client.client.commits) == 1
