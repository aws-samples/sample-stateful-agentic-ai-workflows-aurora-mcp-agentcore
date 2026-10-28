"""The MeridianCatalog Lambda reads the catalog, gates and ranks, and fails closed."""

import _socket
import io
import json
import re
import socket
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError

from tests.lambda_targets_support import TARGETS, load_target
from tests.test_catalog_tools import _names_the_data_api_binds

TARGET = TARGETS / "meridian_catalog"
catalog = load_target("meridian_catalog", "meridian_catalog_lambda")
VECTOR = [0.25] * 1024


def _typed(value):
    """One value as the Data API's typed record format carries it."""
    if value is None:
        return {"isNull": True}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": value}


def typed_response(rows):
    """Rows as ``execute_statement`` answers with ``includeResultMetadata`` (the backend's)."""
    columns = list(dict.fromkeys(key for found in rows for key in found))
    return {"columnMetadata": [{"name": name} for name in columns],
            "records": [[_typed(found.get(name)) for name in columns] for found in rows]}


class FakeDataApi:
    """A boto3 ``rds-data`` client over wire rows, answering each statement by a SQL marker.

    The rows are what the JSON format returns: ``DECIMAL`` as a string, ``NULL`` as null and
    JSONB as JSON text. A request for JSON gets ``formattedRecords`` (the Lambda); any other
    gets the typed ``records`` with column metadata (the backend client), from the same rows.
    """

    def __init__(self, rows_by_marker=None, fail_with=None):
        self.rows_by_marker = rows_by_marker or {}
        self.fail_with = fail_with
        self.requests = []

    @property
    def statements(self):
        return [r["sql"] for r in self.requests]

    def parameters(self, index=-1):
        return {p["name"]: p["value"] for p in self.requests[index].get("parameters", [])}

    def execute_statement(self, **kwargs):
        self.requests.append(kwargs)
        if self.fail_with:
            raise self.fail_with
        rows = next((found for marker, found in self.rows_by_marker.items()
                     if marker in kwargs["sql"]), [])
        if kwargs.get("formatRecordsAs") == "JSON":
            return {"formattedRecords": json.dumps(rows)}
        return typed_response(rows)


class FakeBedrock:
    """Cohere Embed v4 and Rerank 3.5 as Bedrock answers them; either can fail."""

    def __init__(self, rerank_order=None, rerank_error=None, embed_error=None, embed_flakes=0):
        self.rerank_order, self.rerank_error = rerank_order or [], rerank_error
        self.embed_error, self.embed_flakes = embed_error, embed_flakes
        self.requests = []

    def invoke_model(self, **kwargs):
        body = json.loads(kwargs["body"])
        self.requests.append((kwargs["modelId"], body))
        if "texts" in body:
            if self.embed_error:
                raise self.embed_error
            if self.embed_flakes:
                self.embed_flakes -= 1
                raise RuntimeError("embed throttled")
            payload = {"embeddings": {"float": [VECTOR]}}
        else:
            if self.rerank_error:
                raise self.rerank_error
            payload = {"results": [{"index": i, "relevance_score": 1.0 - i / 10}
                                   for i in self.rerank_order]}
        return {"body": io.BytesIO(json.dumps(payload).encode())}


def context(tool):
    return SimpleNamespace(
        client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": tool}))


def call(tool, arguments):
    return catalog.lambda_handler(arguments, context(f"MeridianCatalog___{tool}"))


@pytest.fixture
def world(monkeypatch):
    def build(rows=None, fail_with=None, **bedrock):
        api = FakeDataApi(rows, fail_with)
        fake_bedrock = FakeBedrock(**bedrock)
        monkeypatch.setattr(catalog, "CONFIG", ("arn:cluster", "arn:gateway-secret", "meridian"))
        monkeypatch.setattr(catalog, "RDS", api)
        monkeypatch.setattr(catalog, "BEDROCK", fake_bedrock)
        return api, fake_bedrock
    return build


def row(package_id, price, **extra):
    """A catalog row as the Data API returns it: JSON columns are JSON text."""
    return {"package_id": package_id, "name": f"Trip {package_id}", "operator": "Op",
            "price_per_person": f"{price:.2f}", "description": "d", "image_url": "u",
            "trip_type": "City Breaks", "destination": "Tokyo", "region": "Asia",
            "durations": '["7 nights"]', "highlights": '["h"]', **extra}


def semantic(package_id, price, similarity):
    """A semantic_trip_search row: the function returns no availability and no highlights."""
    found = row(package_id, price, similarity=similarity)
    del found["highlights"]
    return found


# Statements are matched by marker in this order: the semantic function, the lexical arm
# (ts_rank), then the catalog join of every merged candidate (package_id IN).
HYBRID_ROWS = {
    "semantic_trip_search": [semantic("A", 1900.0, 0.9), semantic("B", 2400.0, 0.8),
                             semantic("C", 2600.0, 0.7)],
    "ts_rank": [row("B", 2400.0, availability='{"7 nights": 0}', lexical_score=0.4),
                row("D", 1500.0, availability='{"quantity": 1}', lexical_score=0.3)],
    "WHERE package_id IN": [
        row("A", 1900.0, availability='{"7 nights": 3}'),
        row("B", 2400.0, availability='{"7 nights": 0}'),
        row("C", 2600.0, availability='{"7 nights": 2}'),
        row("D", 1500.0, availability='{"quantity": 1}'),
    ],
}


def ids(result):
    return [p["package_id"] for p in result["packages"]]


def test_an_unknown_tool_is_refused_loudly(world):
    world()
    with pytest.raises(ValueError, match="Unknown Meridian tool"):
        catalog.lambda_handler({}, context("MeridianCatalog___drop_tables"))


def test_hybrid_gates_by_price_and_stock_then_reranks_and_counts(world):
    api, bedrock = world(HYBRID_ROWS, rerank_order=[1, 0])

    result = call("hybrid_trip_search", {"query": "Japan under $2,500", "limit": 5})

    assert ids(result) == ["D", "A"]
    assert result["candidates"] == {"semantic": 3, "lexical": 2, "merged": 4}
    assert result["reranked"] is True
    assert result["packages"][1]["durations"] == ["7 nights"]
    assert result["packages"][0]["availability"] == {"quantity": 1}
    assert [p["rank_delta"] for p in result["packages"]] == [1, -1]
    embed_model, embed_body = bedrock.requests[0]
    assert embed_model == "cohere.embed-v4:0"
    assert embed_body["input_type"] == "search_query"
    assert embed_body["output_dimension"] == 1024
    rerank_model, rerank_body = bedrock.requests[1]
    assert rerank_model == "cohere.rerank-v3-5:0" and rerank_body["top_n"] == 2
    assert api.statements[0].count(":p") == 2


def test_hybrid_uses_the_candidate_pool_for_the_limit(world):
    api, _ = world(HYBRID_ROWS, rerank_order=[0])
    call("hybrid_trip_search", {"query": "Tokyo", "limit": 99})
    assert api.parameters(0)["p1"] == {"longValue": 100}
    api, _ = world(HYBRID_ROWS, rerank_order=[0])
    call("hybrid_trip_search", {"query": "Tokyo", "limit": 0})
    assert api.parameters(0)["p1"] == {"longValue": 25}


def test_hybrid_says_so_when_the_reranker_is_down(world):
    world(HYBRID_ROWS, rerank_error=RuntimeError("rerank offline"))

    result = call("hybrid_trip_search", {"query": "Japan under $2,500", "limit": 5})

    assert result["reranked"] is False
    assert ids(result) == ["A", "D"]
    assert [p["rank_delta"] for p in result["packages"]] == [0, 0]


@pytest.mark.parametrize("order", [[], [-1], [99]], ids=["empty", "negative", "too-high"])
def test_a_reranker_with_no_usable_index_keeps_the_candidate_order(world, order):
    world(HYBRID_ROWS, rerank_order=order)

    result = call("hybrid_trip_search", {"query": "Japan under $2,500", "limit": 5})

    assert ids(result) == ["A", "D"] and result["reranked"] is False


def test_a_duplicate_rerank_index_lists_its_trip_once(world):
    world(HYBRID_ROWS, rerank_order=[1, 1, 0])
    result = call("hybrid_trip_search", {"query": "Japan under $2,500", "limit": 5})
    assert ids(result) == ["D", "A"]


def test_an_embedding_failure_leaves_the_lexical_arm_answering_alone(world):
    api, _ = world(HYBRID_ROWS, embed_error=RuntimeError("embed offline"), rerank_order=[0])

    result = call("hybrid_trip_search", {"query": "Tokyo", "limit": 5})

    assert not any("semantic_trip_search" in sql for sql in api.statements)
    assert result["candidates"] == {"semantic": 0, "lexical": 2, "merged": 2}
    assert ids(result) == ["D"]


def test_a_package_with_no_places_data_is_not_offered(world):
    # Only the semantic arm answers, and its rows carry no availability; the join finds none.
    rows = {"semantic_trip_search": HYBRID_ROWS["semantic_trip_search"]}
    _, bedrock = world(rows, rerank_order=[0])
    result = call("hybrid_trip_search", {"query": "Tokyo"})
    assert result["packages"] == [] and result["reranked"] is False
    assert len(bedrock.requests) == 1, "nothing eligible skips the reranker"


@pytest.mark.parametrize("arguments", [{}, {"query": ""}, {"query": "   "}, {"query": "x" * 501}])
def test_hybrid_needs_a_bounded_query(world, arguments):
    api, bedrock = world(HYBRID_ROWS)
    result = call("hybrid_trip_search", arguments)
    assert result["error"].startswith("query")
    assert api.requests == [] and bedrock.requests == []


def test_compare_reads_at_most_four_and_wraps_the_list(world):
    api, _ = world({"FROM trip_packages": [
        {"package_id": "A", "name": "a", "destination": "T", "region": "Asia", "operator": "O",
         "trip_type": "City", "price_per_person": "10.00", "durations": '["2"]',
         "highlights": '["h1","h2","h3","h4"]'}]})

    result = call("compare_packages", {"package_ids": ["A", "B", "C", "D", "E"]})

    assert result["result"][0]["top_highlights"] == ["h1", "h2", "h3"]
    assert result["result"][0]["durations"] == ["2"]
    assert len(api.requests[0]["parameters"]) == 4


def test_compare_with_no_ids_reads_nothing(world):
    api, _ = world()
    assert call("compare_packages", {"package_ids": []}) == {"result": []}
    assert api.requests == []


@pytest.mark.parametrize("arguments", [{"package_ids": "A"}, {"package_ids": [1]}, {}])
def test_compare_refuses_a_malformed_id_list(world, arguments):
    api, _ = world()
    assert call("compare_packages", arguments)["error"].startswith("package_ids")
    assert api.requests == []


def test_price_range_reads_the_aggregate_row_and_says_it_is_not_seasonal(world):
    world({"MIN(price_per_person)": [{"min_price": 1199.0, "avg_price": 1949.5,
                                      "max_price": 3299.0, "sample_size": 4}]})
    result = call("price_range", {"destination": "Tokyo"})
    assert (result["low"], result["average"], result["high"]) == (1199.0, 1949.5, 3299.0)
    assert "not seasonal" in result["note"].lower()


def test_price_range_with_no_rows_invents_nothing(world):
    world({"MIN(price_per_person)": [{"sample_size": 0}]})
    assert call("price_range", {"destination": "Atlantis"}) == {
        "destination": "Atlantis", "sample_size": 0}


def test_region_inventory_sums_places(world):
    api, _ = world({"WHERE region ILIKE": [
        {"package_id": "A", "trip_type": "City", "availability": '{"a": 3, "b": 1}'},
        {"package_id": "B", "trip_type": None, "availability": None}]})
    result = call("region_inventory", {"region": "Asia", "trip_type": "City"})
    assert result["package_count"] == 2 and result["total_departure_slots"] == 4
    assert result["package_count_by_trip_type"] == {"City": 1, "Other": 1}
    assert api.statements[0].rstrip().endswith("AND trip_type = :p1")


def test_currency_convert_and_its_answer_for_an_unknown_currency(world):
    world()
    assert call("currency_convert", {"amount": 100, "from_ccy": "eur", "to_ccy": "JPY"})[
        "converted"] == round(100 / 0.92 * 156.4, 2)
    answer = call("currency_convert", {"amount": 1, "from_ccy": "XXX", "to_ccy": "USD"})
    assert answer["error"] == "unsupported_currency" and "USD" in answer["supported"]


def test_search_trips_binds_every_value_as_a_parameter(world):
    api, _ = world({"FROM trip_packages": [row("A", 100.0)]})

    result = call("search_trips", {"trip_type": "City Breaks'; DROP TABLE x; --",
                                   "max_price_per_person": 2000, "limit": 3})

    sql = api.statements[0]
    assert "DROP TABLE" not in sql and "City Breaks" not in sql
    values = api.parameters()
    assert values["p0"] == {"stringValue": "City Breaks'; DROP TABLE x; --"}
    assert result["parameters"][0] == "City Breaks'; DROP TABLE x; --"
    assert "$1" in result["sql"] and "$3" in result["sql"]
    assert result["packages"][0]["package_id"] == "A"


def test_search_trips_matches_free_text_with_the_like_wildcards_made_literal(world):
    api, _ = world({"FROM trip_packages": [row("A", 100.0)]})

    result = call("search_trips", {"text": "100% Tokyo", "limit": 3})

    sql = api.statements[0]
    assert sql.count("ILIKE :p") == 5 and "ESCAPE" not in sql
    pattern = {"stringValue": "%100\\% Tokyo%"}
    assert [api.parameters()[f"p{i}"] for i in range(5)] == [pattern] * 5
    assert result["parameters"] == ["%100\\% Tokyo%"] * 5 + [3]


def test_every_statement_the_lambda_sends_binds_every_parameter(world):
    """The Data API's own literal scan: a backslash before a quote would swallow the rest."""
    api, _ = world({**HYBRID_ROWS, "FROM trip_packages": [row("A", 1.0)]}, rerank_order=[0])
    call("hybrid_trip_search", {"query": "Tokyo under $3,000"})
    call("search_trips", {"trip_type": "City Breaks", "max_price_per_person": 900,
                          "text": "a_b\\c", "limit": 2})
    call("compare_packages", {"package_ids": ["A", "B"]})
    call("price_range", {"destination": "100% Tokyo"})
    call("region_inventory", {"region": "Asia", "trip_type": "City Breaks"})
    for request in api.requests:
        named = [p["name"] for p in request.get("parameters", [])]
        assert named and _names_the_data_api_binds(request["sql"]) == named, request["sql"]


WRITE_VERBS = r"(?:insert|update|delete|merge|drop|alter|create|grant|revoke|truncate|copy|call)"


def _is_one_read(sql):
    """True for a single SELECT, or WITH ... SELECT whose CTEs do not write.

    Parser-light: comments stripped, whitespace normalized, case folded, string literals
    blanked; a statement counts only when nothing follows its one optional final semicolon.
    """
    text = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL)
    text = re.sub(r"'(?:[^']|'')*'", "''", text)
    text = " ".join(text.lower().split()).rstrip(" ;")
    if ";" in text:
        return False
    if not text.startswith(("select", "with")):
        return False
    return re.search(rf"\b{WRITE_VERBS}\b", text) is None


@pytest.mark.parametrize("sql", [
    "SELECT 1", "select a from t where b = %s;", "WITH q AS (SELECT 1) SELECT * FROM q",
    "SELECT 'delete me; drop it' FROM t",
])
def test_the_read_check_accepts_a_read(sql):
    assert _is_one_read(sql)


@pytest.mark.parametrize("sql", [
    "SELECT 1; DELETE FROM trip_packages", "SELECT 1;DELETE FROM trip_packages",
    "select 1 -- x\n; update t set a = 1", "SELECT 1 /* x */ ; INSERT INTO t VALUES (1)",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
    "with u as (update t set a = 1 returning *) select * from u",
    "WITH i AS (\n INSERT INTO t VALUES (1) RETURNING 1) SELECT 1",
    "DELETE FROM t", "SELECT 1 WHERE x @@ q.tsq; DELETE FROM t",
])
def test_the_read_check_rejects_a_second_statement_and_a_writing_cte(sql):
    assert not _is_one_read(sql)


def test_every_statement_the_lambda_sends_is_a_read(world):
    api, _ = world({**HYBRID_ROWS, "FROM trip_packages": [row("A", 1.0)]}, rerank_order=[0])
    call("hybrid_trip_search", {"query": "Tokyo"})
    call("search_trips", {"text": "x", "trip_type": "City", "max_price_per_person": 5})
    call("compare_packages", {"package_ids": ["A"]})
    call("price_range", {"destination": "Tokyo"})
    call("region_inventory", {"region": "Asia", "trip_type": "City"})
    assert len(api.statements) == 7
    for sql in api.statements:
        assert _is_one_read(sql), sql


def test_parameters_use_the_login_and_the_data_api_formats(world):
    api, _ = world({"FROM trip_packages": [row("A", 1.0)]})
    call("search_trips", {"limit": 2})
    request = api.requests[0]
    assert request["secretArn"] == "arn:gateway-secret" and request["formatRecordsAs"] == "JSON"
    assert request["resourceArn"] == "arn:cluster" and request["database"] == "meridian"


def test_a_failing_read_names_the_tool_and_never_the_exception_text(world, capsys):
    world(fail_with=RuntimeError("password=hunter2 at 10.0.0.1"))

    result = call("price_range", {"destination": "Tokyo"})

    assert result == {"error": "catalog_tool_failed", "tool": "price_range"}
    out = capsys.readouterr().out
    assert "hunter2" not in out and "RuntimeError" in out


def test_one_failure_log_line_carries_the_tool_the_request_id_and_a_masked_detail(
        world, capsys):
    secret = "arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian-gateway-AbCdEf"
    world(fail_with=RuntimeError(f"AccessDenied for {secret} in account 999999999999 " + "x" * 400))
    tool_context = context("MeridianCatalog___price_range")
    tool_context.client_context.custom["bedrockAgentCoreAwsRequestId"] = "req-1"

    result = catalog.lambda_handler({"destination": "Tokyo"}, tool_context)

    assert result == {"error": "catalog_tool_failed", "tool": "price_range"}
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    failure = next(line for line in lines if line.get("ok") is False)
    assert failure["meridian_tool"] == "price_range"
    assert failure["gatewayRequestId"] == "req-1" and failure["error_type"] == "RuntimeError"
    detail = failure["detail"]
    assert "123456789012" not in detail and "999999999999" not in detail
    assert "secretsmanager" not in detail and "<arn>" in detail and "<acct>" in detail
    assert len(detail) <= 120


def test_a_non_numeric_price_ceiling_is_an_input_error_not_a_failure(world, capsys):
    api, _ = world()
    result = call("search_trips", {"max_price_per_person": "cheap"})
    assert result == {"error": "max_price_per_person must be a number"}
    assert api.requests == [] and "catalog_tool_failed" not in capsys.readouterr().out


def test_a_bad_availability_value_fails_the_tool_and_logs_the_package(world, capsys):
    world({"WHERE region ILIKE": [
        {"package_id": "A", "trip_type": "City", "availability": '{"a": "many"}'}]})
    result = call("region_inventory", {"region": "Asia"})
    assert result == {"error": "catalog_tool_failed", "tool": "region_inventory"}
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    failure = next(line for line in lines if line.get("ok") is False)
    assert "package A" in failure["detail"] and failure["error_type"] == "ValueError"


def test_an_unsupported_currency_is_logged_as_an_answer_and_bad_input_as_a_failure(
        world, capsys):
    world()
    call("currency_convert", {"amount": 1, "from_ccy": "XXX", "to_ccy": "USD"})
    call("currency_convert", {"amount": "x", "from_ccy": "USD", "to_ccy": "EUR"})
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()
                 if '"gatewayRequestId"' in line]
    assert [line["ok"] for line in summaries] == [True, False]


EVERY_ARGUMENT = {
    "search_trips": {"trip_type": "City Breaks", "max_price_per_person": 1500.0, "limit": 7,
                     "text": "Kyoto"},
    "compare_packages": {"package_ids": ["A", "B"]},
    "price_range": {"destination": "Tokyo"},
    "region_inventory": {"region": "Asia", "trip_type": "City Breaks"},
    "currency_convert": {"amount": 100, "from_ccy": "EUR", "to_ccy": "JPY"},
    "hybrid_trip_search": {"query": "Tokyo", "limit": 10},
}


def _effect(tool, api, result):
    """What only the argument named in EVERY_ARGUMENT could have caused."""
    if tool == "search_trips":
        assert result["parameters"] == ["City Breaks", 1500.0] + ["%Kyoto%"] * 5 + [7]
    elif tool == "compare_packages":
        assert [p["value"] for p in api.requests[0]["parameters"]] == [
            {"stringValue": "A"}, {"stringValue": "B"}]
    elif tool == "price_range":
        assert api.parameters()["p0"] == {"stringValue": "%Tokyo%"}
    elif tool == "region_inventory":
        assert api.parameters() == {"p0": {"stringValue": "%Asia%"},
                                    "p1": {"stringValue": "City Breaks"}}
    elif tool == "currency_convert":
        assert result["converted"] == round(100 / 0.92 * 156.4, 2)
    else:
        assert api.parameters(0)["p1"] == {"longValue": 50}


def test_every_property_of_every_tool_is_one_the_tool_reads(world):
    """Send all properties, optional ones too: a renamed argument on either side fails here."""
    schema = json.loads((TARGET / "tool-schema.json").read_text())
    assert [t["name"] for t in schema] == list(catalog.TOOLS)
    for tool in schema:
        name = tool["name"]
        assert set(tool["inputSchema"]["properties"]) == set(EVERY_ARGUMENT[name]), name
        api, _ = world({**HYBRID_ROWS, "FROM trip_packages": [row("A", 1.0)]}, rerank_order=[0])
        result = call(name, EVERY_ARGUMENT[name])
        assert "error" not in result, (name, result)
        _effect(name, api, result)


def test_the_bedrock_client_fails_fast_so_the_fallbacks_fit_in_the_function_timeout(monkeypatch):
    """One attempt in all, short timeouts: a hung model call gives way to the fallback.

    The function runs 30 s and the Runtime waits 35 s. One search makes EMBEDDING_ATTEMPTS
    embedding calls and one rerank call, each at most (connect + read) seconds, which must
    leave room for the Data API reads. The retry setting is read off the BUILT client.
    """
    monkeypatch.setattr(catalog, "CONFIG", ("arn:cluster", "arn:gateway-secret", "meridian"))
    monkeypatch.setattr(catalog, "RDS", object())
    monkeypatch.setattr(catalog, "BEDROCK", None)

    catalog._boot()

    config = catalog.BEDROCK.meta.config
    assert (config.connect_timeout, config.read_timeout) == (2, 5)
    assert config.retries["total_max_attempts"] == 1
    calls = catalog.EMBEDDING_ATTEMPTS + 1
    worst_case = calls * config.retries["total_max_attempts"] * (
        config.connect_timeout + config.read_timeout)
    assert worst_case <= 22, "leave at least 8 s of the 30 s function timeout for the reads"


def test_a_bedrock_call_to_a_dead_endpoint_is_sent_once_and_not_retried(monkeypatch):
    """Behaviour, not settings: botocore's legacy retries would send a second request.

    The suite's network guard refuses every connect; this test lets one through, to the
    loopback port it just closed, so the refusal is real and no other address is reachable.
    """
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    guarded = socket.socket.connect

    def connect_to_the_dead_port_only(sock, address):
        if address == ("127.0.0.1", port):
            return _socket.socket.connect(sock, address)
        return guarded(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect_to_the_dead_port_only)
    client = boto3.client("bedrock-runtime", region_name="us-east-1",
                          endpoint_url=f"http://127.0.0.1:{port}", config=catalog.BEDROCK_CONFIG)
    sent = []
    client.meta.events.register("before-send.bedrock-runtime.InvokeModel",
                                lambda request, **_: sent.append(request.url))

    with pytest.raises(EndpointConnectionError):
        client.invoke_model(modelId="cohere.embed-v4:0", body="{}")

    assert len(sent) == 1
