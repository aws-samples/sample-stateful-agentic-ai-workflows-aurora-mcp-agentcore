"""Binary parameters must survive the Data API unchanged.

boto3 base64-transcodes blobValue itself, so the client passes and receives
raw bytes. Any extra encoding in application code is a double-encoding bug,
and the previous fallback stored the Python repr of the bytes object.

This file also holds the regression tests for `_format_parameters`'s
table-driven encoder, covering every parameter type it dispatches on.
"""

import json
from decimal import Decimal

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


def test_none_encodes_as_null(client: RDSDataClient) -> None:
    formatted = client._format_parameters((None,))
    assert formatted == [{"name": "p0", "value": {"isNull": True}}]


def test_bool_encodes_as_boolean_not_long(client: RDSDataClient) -> None:
    """bool is a subclass of int; it must never fall through to longValue."""
    formatted = client._format_parameters((True, False))
    assert formatted[0] == {"name": "p0", "value": {"booleanValue": True}}
    assert formatted[1] == {"name": "p1", "value": {"booleanValue": False}}
    assert "longValue" not in formatted[0]["value"]
    assert "longValue" not in formatted[1]["value"]


def test_int_encodes_as_long_value(client: RDSDataClient) -> None:
    formatted = client._format_parameters((42,))
    assert formatted == [{"name": "p0", "value": {"longValue": 42}}]


def test_float_encodes_as_double_value(client: RDSDataClient) -> None:
    formatted = client._format_parameters((3.14,))
    assert formatted == [{"name": "p0", "value": {"doubleValue": 3.14}}]


def test_decimal_encodes_string_value_with_decimal_type_hint(client: RDSDataClient) -> None:
    formatted = client._format_parameters((Decimal("19.99"),))
    assert formatted == [
        {"name": "p0", "value": {"stringValue": "19.99"}, "typeHint": "DECIMAL"}
    ]
    assert formatted[0]["typeHint"] == "DECIMAL"


@pytest.mark.parametrize("value", [["a", "b"], {"k": "v"}])
def test_list_and_dict_encode_as_json_string(client: RDSDataClient, value) -> None:
    formatted = client._format_parameters((value,))
    assert formatted == [{"name": "p0", "value": {"stringValue": json.dumps(value)}}]


def test_unrecognized_type_falls_back_to_string_value(client: RDSDataClient) -> None:
    class Unrecognized:
        def __str__(self) -> str:
            return "unrecognized-repr"

    formatted = client._format_parameters((Unrecognized(),))
    assert formatted == [{"name": "p0", "value": {"stringValue": "unrecognized-repr"}}]


def test_mixed_tuple_preserves_positional_names(client: RDSDataClient) -> None:
    formatted = client._format_parameters((None, True, 1, 2.5, Decimal("1.5"), b"\x01"))
    assert [p["name"] for p in formatted] == ["p0", "p1", "p2", "p3", "p4", "p5"]
    assert formatted[0]["value"] == {"isNull": True}
    assert formatted[1]["value"] == {"booleanValue": True}
    assert formatted[2]["value"] == {"longValue": 1}
    assert formatted[3]["value"] == {"doubleValue": 2.5}
    assert formatted[4]["value"] == {"stringValue": "1.5"}
    assert formatted[4]["typeHint"] == "DECIMAL"
    assert formatted[5]["value"] == {"blobValue": b"\x01"}


def test_empty_and_absent_params_return_empty_list(client: RDSDataClient) -> None:
    assert client._format_parameters(()) == []
    assert client._format_parameters(None) == []
