"""The backend and the holds Lambda agree on which Data API failures were never sent.

The Lambda deploys alone, so it repeats the backend's rule. Both judge one table of errors built
as botocore raises them; tests/test_data_api_tls_drop.py produces the TLS chains from a real server.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from backend.db.rds_data_client import DATA_API_RETRY_BACKOFF_SECONDS, never_sent
from tests.test_data_api_call_retry import URL, tls_broken_record, tls_eof

TARGET = (
    Path(__file__).resolve().parents[1]
    / "meridian_agentcore" / "agentcore" / "gateway_targets" / "meridian_holds"
)
sys.path.insert(0, str(TARGET))

import lambda_function as holds  # noqa: E402

CASES = [
    ("tls eof", tls_eof, True),
    ("connect timeout", lambda: ConnectTimeoutError(endpoint_url=URL), True),
    ("no endpoint", lambda: EndpointConnectionError(endpoint_url=URL), True),
    ("tls broken record", tls_broken_record, False),
    ("connection closed", lambda: ConnectionClosedError(endpoint_url=URL), False),
    ("read timeout", lambda: ReadTimeoutError(endpoint_url=URL), False),
    ("throttled", lambda: ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "Execute"), False),
]


@pytest.mark.parametrize(("make", "expected"), [(m, e) for _, m, e in CASES],
                         ids=[name for name, _, _ in CASES])
def test_both_copies_judge_the_error_the_same(make, expected):
    assert never_sent(make()) is expected
    assert holds._never_sent(make()) is expected


def test_both_copies_wait_the_same_between_attempts():
    assert holds.UNSENT_RETRY_DELAYS == DATA_API_RETRY_BACKOFF_SECONDS
