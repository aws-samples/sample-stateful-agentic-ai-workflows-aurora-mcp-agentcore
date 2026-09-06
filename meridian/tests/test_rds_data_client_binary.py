"""Binary parameters must survive the Data API unchanged.

boto3 base64-transcodes blobValue itself, so the client passes and receives
raw bytes. Any extra encoding in application code is a double-encoding bug,
and the previous fallback stored the Python repr of the bytes object.
"""

import pytest

from backend.db.rds_data_client import RDSDataClient


@pytest.fixture
def client() -> RDSDataClient:
    return RDSDataClient.__new__(RDSDataClient)


def test_bytes_become_blob_value(client: RDSDataClient) -> None:
    payload = b"\x80\x03\x00\xff binary \n\t"
    formatted = client._format_parameters((payload,))
    assert formatted == [{"name": "p0", "value": {"blobValue": payload}}]


def test_bytes_are_not_stringified(client: RDSDataClient) -> None:
    formatted = client._format_parameters((b"\x80abc",))
    assert "stringValue" not in formatted[0]["value"]


def test_blob_value_parses_back_to_bytes(client: RDSDataClient) -> None:
    assert client._parse_value({"blobValue": b"\x00\x01\xfe"}) == b"\x00\x01\xfe"


def test_full_byte_range_round_trips(client: RDSDataClient) -> None:
    payload = bytes(range(256))
    formatted = client._format_parameters((payload,))
    assert client._parse_value(formatted[0]["value"]) == payload


def test_embedded_nulls_survive(client: RDSDataClient) -> None:
    payload = b"before\x00after\x00"
    formatted = client._format_parameters((payload,))
    assert client._parse_value(formatted[0]["value"]) == payload


def test_bytearray_and_memoryview_are_coerced(client: RDSDataClient) -> None:
    for value in (bytearray(b"\x01\x02"), memoryview(b"\x01\x02")):
        formatted = client._format_parameters((value,))
        assert formatted[0]["value"] == {"blobValue": b"\x01\x02"}
