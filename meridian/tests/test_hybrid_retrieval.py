"""The one hybrid search: pure helpers, the arms, the gate, the fallback, the labels."""

import re
from pathlib import Path

import pytest

from backend.agents.phase_03_retrieval.search_agent import SearchAgent
from backend.agents.phase_05_workflow import memory_recall
from backend.config import config
from backend.db.embedding_service import EmbeddingUnavailable
from backend.retrieval import hybrid
from backend.trace_names import RETRIEVAL_CODE

ACCOUNT = "123456789012"


def row(package_id, price=1800.0, places=3, **extra):
    return {
        "package_id": package_id, "name": f"Trip {package_id}", "operator": "Costa",
        "price_per_person": price, "description": "Villa", "image_url": "",
        "trip_type": "Beach", "destination": "Positano", "region": "Europe",
        "durations": ["5 nights"], "availability": {"5 nights": places}, "highlights": ["Sea"],
        **extra,
    }


class FakeDb:
    def __init__(self, semantic=(), lexical=(), details=()):
        self.semantic, self.lexical, self.details = list(semantic), list(lexical), list(details)
        self.calls = []

    async def execute(self, sql, params=None, transaction_id=None):
        self.calls.append((" ".join(sql.split()), tuple(params or ())))
        if "semantic_trip_search" in sql:
            return list(self.semantic)
        if "ts_rank" in sql:
            return list(self.lexical)
        if "package_id IN" in sql:
            return [d for d in self.details if d["package_id"] in params]
        raise AssertionError(f"unexpected statement: {sql}")


class FakeEmbeddings:
    def __init__(self, rerank=None, unavailable=False):
        self.input_types, self.rerank_calls = [], []
        self._rerank, self._unavailable = rerank, unavailable

    def generate_text_embedding(self, text, input_type="search_document"):
        self.input_types.append(input_type)
        if self._unavailable:
            raise EmbeddingUnavailable("cohere.embed-v4:0 did not answer after 2 attempts")
        return [0.25] * 4

    def rerank_documents(self, query, documents, top_n=5):
        self.rerank_calls.append((query, list(documents), top_n))
        if isinstance(self._rerank, Exception):
            raise self._rerank
        if self._rerank is None:
            return [{"index": i, "score": 0.9 - i / 10} for i in range(len(documents))][:top_n]
        return self._rerank


@pytest.fixture
def run(monkeypatch):
    async def go(db, embeddings, query="quiet villa under $2,000", limit=5):
        monkeypatch.setattr(hybrid, "get_rds_data_client", lambda: db)
        monkeypatch.setattr(hybrid, "get_embedding_service", lambda: embeddings)
        return await hybrid.retrieval_rows(query, limit)

    return go


def titles(spans):
    return [span.title for span in spans]


# ---------------------------------------------------------------- pure helpers

def test_merge_keeps_semantic_order_then_adds_lexical_only_rows_and_scores():
    semantic = [row("A", similarity=0.9), row("B", similarity=0.8)]
    lexical = [row("B", lexical_score=0.4), row("C", lexical_score=0.3)]

    merged = hybrid.merge_by_package(semantic, lexical)

    assert [r["package_id"] for r in merged] == ["A", "B", "C"]
    assert merged[1]["lexical_score"] == 0.4 and merged[1]["similarity"] == 0.8
    assert "lexical_score" not in merged[0]


def test_gate_drops_over_budget_sold_out_and_unknown_and_keeps_order():
    rows = [row("A", 1500), row("B", 2600), row("C", 1700), row("D", 1600), row("E", 1000)]
    places = {"A": 2, "B": 5, "C": 0, "D": 1}   # E is missing: unknown stock is not stock

    kept = hybrid.apply_gate(rows, 2000.0, places)

    assert [r["package_id"] for r in kept] == ["A", "D"]


def test_gate_without_a_ceiling_still_requires_places():
    kept = hybrid.apply_gate([row("A", 9000), row("B", 100)], None, {"A": 1, "B": 0})
    assert [r["package_id"] for r in kept] == ["A"]


@pytest.mark.parametrize(("availability", "total"), [
    ({"5 nights": 3, "7 nights": 2}, 5), ({"quantity": 7}, 7), ({}, 0), (None, 0),
    ('{"5 nights": 4}', 4), ("not json", 0), ({"5 nights": "x"}, 0), ({"quantity": "x"}, 0),
])
def test_total_places(availability, total):
    assert hybrid.total_places(availability) == total


def test_rerank_document_joins_the_five_fields_in_order():
    document = hybrid.rerank_document(row("A", name="N", destination="D", operator="O"))
    assert document == "N | D | Beach | O | Villa"


@pytest.mark.parametrize(("limit", "pool"), [(1, 25), (5, 25), (10, 50)])
def test_candidate_limit_floors_at_25(limit, pool):
    assert config.search.rerank_candidate_multiplier == 5
    assert hybrid.candidate_limit(limit) == pool


# --------------------------------------------------------------------- the flow

async def test_the_query_is_embedded_as_a_search_query(run):
    embeddings = FakeEmbeddings()
    await run(FakeDb(), embeddings)
    assert embeddings.input_types == ["search_query"]


async def test_over_budget_and_sold_out_candidates_never_reach_the_reranker(run):
    semantic = [row("OK", 1800), row("DEAR", 2600), row("GONE", 1700)]
    details = [row("OK", 1800), row("DEAR", 2600), row("GONE", 1700, places=0)]
    embeddings = FakeEmbeddings()

    ranked, spans, _counts = await run(FakeDb(semantic=semantic, details=details), embeddings)

    assert [r["package_id"] for r in ranked] == ["OK"]
    assert len(embeddings.rerank_calls[0][1]) == 1
    gate = next(s for s in spans if s.title == "Eligibility filter applied")
    assert "Dropped 2 of 3" in gate.details and "over the $2,000 budget" in gate.details


async def test_unreachable_embeddings_leave_the_lexical_arm_answering_alone(run):
    db = FakeDb(lexical=[row("LEX")], details=[row("LEX")])

    ranked, spans, counts = await run(db, FakeEmbeddings(unavailable=True))

    assert [r["package_id"] for r in ranked] == ["LEX"]
    assert not any("semantic_trip_search" in sql for sql, _ in db.calls)
    assert "Semantic arm unavailable, lexical only" in titles(spans)
    assert counts["semantic"] == 0 and counts["lexical"] == 1


async def test_a_reranker_failure_keeps_candidate_order_and_says_so_without_the_account_id(run):
    failure = RuntimeError(
        f"AccessDenied on arn:aws:bedrock:us-east-1:{ACCOUNT}:inference-profile/x"
    )
    semantic = [row("A", similarity=0.9), row("B", similarity=0.8)]
    details = [row("A"), row("B")]

    ranked, spans, counts = await run(FakeDb(semantic=semantic, details=details),
                                      FakeEmbeddings(rerank=failure))

    assert [r["package_id"] for r in ranked] == ["A", "B"]
    assert [r["rank_delta"] for r in ranked] == [0, 0]
    span = next(s for s in spans if s.title.startswith("Cohere rerank"))
    assert span.title == "Cohere rerank unavailable, using candidate order"
    assert "semantic" not in span.title.lower() and "semantic rank" not in span.details
    assert "<acct>" in span.details and ACCOUNT not in span.details
    assert counts["reranked"] is False


async def test_a_reranker_that_returns_nothing_is_a_failure_not_an_empty_answer(run):
    ranked, spans, counts = await run(
        FakeDb(semantic=[row("A")], details=[row("A")]), FakeEmbeddings(rerank=[]))
    assert [r["package_id"] for r in ranked] == ["A"]
    assert "Cohere rerank unavailable, using candidate order" in titles(spans)
    assert counts["reranked"] is False


@pytest.mark.parametrize("ranked", [[{"index": 9}], [{"index": -1}]], ids=["too-high", "negative"])
async def test_a_reranker_with_no_usable_index_is_a_failure_not_an_empty_answer(run, ranked):
    semantic = [row("A", similarity=0.9), row("B", similarity=0.8)]
    details = [row("A"), row("B")]

    result, spans, counts = await run(FakeDb(semantic=semantic, details=details),
                                      FakeEmbeddings(rerank=ranked))

    assert [r["package_id"] for r in result] == ["A", "B"]
    assert [r["rank_delta"] for r in result] == [0, 0]
    assert "Cohere rerank unavailable, using candidate order" in titles(spans)
    assert "Cohere rerank applied" not in titles(spans)
    assert counts["reranked"] is False


async def test_the_reranker_order_scores_and_movement_are_carried(run):
    semantic = [row("A", similarity=0.9), row("B", similarity=0.8)]
    details = [row("A"), row("B")]
    rerank = [{"index": 1, "score": 0.97}, {"index": 0, "score": 0.12}]

    ranked, spans, counts = await run(FakeDb(semantic=semantic, details=details),
                                      FakeEmbeddings(rerank=rerank))

    assert [r["package_id"] for r in ranked] == ["B", "A"]
    assert [r["similarity"] for r in ranked] == [0.97, 0.12]
    assert [r["rank_delta"] for r in ranked] == [1, -1]
    assert ranked[0]["pre_rerank_position"] == 1 and ranked[0]["pre_rerank_similarity"] == 0.8
    assert "Cohere rerank applied" in titles(spans)
    assert counts["reranked"] is True


async def test_nothing_eligible_skips_the_reranker(run):
    embeddings = FakeEmbeddings()
    ranked, spans, counts = await run(FakeDb(), embeddings)
    assert ranked == [] and embeddings.rerank_calls == []
    assert "Cohere rerank skipped" in titles(spans)
    assert titles(spans)[-1] == "Hybrid retrieval returned 0 results"
    assert counts == {"semantic": 0, "lexical": 0, "merged": 0, "eligible": 0, "reranked": False}


async def test_the_counts_come_from_the_arms_the_merge_and_the_gate(run):
    semantic = [row("A"), row("B"), row("DEAR", 2600)]
    lexical = [row("B", lexical_score=0.4), row("C", lexical_score=0.3)]
    details = [row("A"), row("B"), row("DEAR", 2600), row("C", places=0)]

    ranked, _spans, counts = await run(
        FakeDb(semantic=semantic, lexical=lexical, details=details), FakeEmbeddings(), limit=1)

    assert counts == {"semantic": 3, "lexical": 2, "merged": 4, "eligible": 2, "reranked": True}
    assert len(ranked) == 1, "eligible counts the gate's survivors, not the returned page"


async def test_the_spans_show_placeholders_and_values_and_are_labelled_as_code(run):
    semantic = [row("A", similarity=0.9)]
    _ranked, spans, _counts = await run(
        FakeDb(semantic=semantic, details=[row("A")]), FakeEmbeddings())

    assert {span.agent_name for span in spans} == {RETRIEVAL_CODE}
    fetched = next(s for s in spans if s.title == "Hybrid candidates fetched")
    semantic_statement, lexical_statement = fetched.sql_query.split("\n\n")
    assert "semantic_trip_search($1::vector, $2::integer)" in semantic_statement
    assert semantic_statement.endswith("-- Values: $1 = '[0.25,0.25,0.25,0.25]', $2 = 25")
    assert "websearch_to_tsquery('english', $1)" in lexical_statement
    assert lexical_statement.endswith("-- Values: $1 = 'quiet villa under $2,000', $2 = 25")
    assert "quiet villa" not in lexical_statement.split("-- Values:")[0]
    hydrated = next(s for s in spans if s.title == "Catalog card details hydrated")
    assert "package_id IN ($1)" in hydrated.sql_query
    assert "$1 = 'A'" in hydrated.sql_query


async def test_retrieval_search_returns_products_with_rank_movement(monkeypatch):
    db = FakeDb(semantic=[row("A", similarity=0.9), row("B", similarity=0.8)],
                details=[row("A"), row("B")])
    reversed_by_reranker = [{"index": 1, "score": 0.42}, {"index": 0, "score": 0.11}]
    monkeypatch.setattr(hybrid, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(hybrid, "get_embedding_service",
                        lambda: FakeEmbeddings(rerank=reversed_by_reranker))

    products, spans = await hybrid.retrieval_search("quiet villa", limit=3)

    assert [p.product_id for p in products] == ["B", "A"]
    assert [p.rank_delta for p in products] == [1, -1]
    assert [p.pre_rerank_position for p in products] == [1, 0]
    assert [p.similarity for p in products] == [0.42, 0.11] and spans


def test_package_payload_is_the_shape_the_model_receives():
    payload = hybrid.package_payload(row("A", similarity=0.5, rank_delta=2,
                                         pre_rerank_position=3, pre_rerank_similarity=0.4))
    assert payload["package_id"] == "A" and payload["price_per_person"] == 1800.0
    assert payload["rank_delta"] == 2 and payload["pre_rerank_position"] == 3
    assert payload["durations"] == ["5 nights"] and payload["highlights"] == ["Sea"]


async def test_the_phase_3_tool_runs_the_shared_module_and_forwards_its_spans(monkeypatch):
    db = FakeDb(semantic=[row("A", similarity=0.9)], details=[row("A")])
    embeddings = FakeEmbeddings()
    monkeypatch.setattr(hybrid, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(hybrid, "get_embedding_service", lambda: embeddings)
    agent = SearchAgent.__new__(SearchAgent)
    spans = []
    agent.activity_callback = spans.append

    result = await agent.hybrid_search("quiet villa under $2,000", limit=3)

    assert [p["package_id"] for p in result["packages"]] == ["A"]
    assert result["query"] == "quiet villa under $2,000"
    assert embeddings.input_types == ["search_query"]
    assert "Hybrid candidates fetched" in titles(spans)
    assert {span.agent_name for span in spans} == {RETRIEVAL_CODE}
    assert not hasattr(agent, "db") and not hasattr(agent, "embedding_service")


def test_phase_5_and_phase_3_call_the_same_function():
    assert memory_recall.retrieval_search is hybrid.retrieval_search
    source = Path("backend/agents/phase_05_workflow/service.py").read_text()
    assert "from backend.retrieval.hybrid import retrieval_search" in source


MERIDIAN = Path(__file__).resolve().parents[1]
# The vector function's readers: the hybrid module and its dependency-free Lambda port. The
# Workflow Runtime's staged copy of backend/ is generated and not scanned.
VECTOR_FUNCTION_READERS = {
    "backend/retrieval/hybrid.py",
    "meridian_agentcore/agentcore/gateway_targets/meridian_catalog/lambda_function.py",
    # The retired target's Lambda reads it too until Task 14 deletes that folder and this line.
    "meridian_agentcore/agentcore/gateway_targets/semantic_trip_search/lambda_function.py",
}


# Code a Runtime stages from backend/ or generates at build time; the originals are scanned.
STAGED_COPIES = (
    "meridian_agentcore/app/MeridianWorkflow/backend",
    "meridian_agentcore/app/MeridianConcierge/concierge_definition.py",
)
SCANNED_ROOTS = (
    "backend", "meridian_agentcore/agentcore/gateway_targets", "meridian_agentcore/app")
# A call of the function in SQL, any case: the name followed by "(", after the keyword FROM,
# JOIN, SELECT or LATERAL or after a comma, with optional space, a "(" (ROWS FROM (, SELECT (),
# and an optional schema, each part optionally double-quoted. Prose such as "call
# semantic_trip_search(query)" has none of those before the name and is not matched.
VECTOR_CALL = re.compile(
    r'(?:\bfrom|\bjoin|\bselect|\blateral|,)\s*\(?\s*(?:"?\w+"?\.)?"?semantic_trip_search"?\s*\(',
    re.IGNORECASE)
VECTOR_STATEMENT = re.compile(r"\bSEMANTIC_SQL\b")


def vector_function_readers(base):
    """Relative paths under ``base`` whose Python source reads the vector function."""
    staged = tuple(base / name for name in STAGED_COPIES)
    readers = set()
    for root in SCANNED_ROOTS:
        for path in (base / root).rglob("*.py"):
            if any(path == copy or copy in path.parents for copy in staged):
                continue
            text = path.read_text()
            if VECTOR_CALL.search(text) or VECTOR_STATEMENT.search(text):
                readers.add(path.relative_to(base).as_posix())
    return readers


def test_only_the_hybrid_module_and_its_lambda_port_query_the_vector_function():
    assert vector_function_readers(MERIDIAN) == VECTOR_FUNCTION_READERS


@pytest.mark.parametrize("source", [
    'SQL = "SELECT * FROM semantic_trip_search(%s::vector, 5)"',
    'SQL = "select * from semantic_trip_search (%s::vector, 5)"',
    'SQL = "SELECT * FROM public.semantic_trip_search(%s)"',
    'SQL = """SELECT *\n  FROM\n  SEMANTIC_TRIP_SEARCH(%s)"""',
    'SQL = "SELECT semantic_trip_search(%s)"',
    'SQL = "SELECT * FROM t,semantic_trip_search(%s)"',
    'SQL = \'SELECT * FROM "semantic_trip_search"(%s)\'',
    'SQL = \'SELECT * FROM "public"."semantic_trip_search"(%s)\'',
    'SQL = "SELECT * FROM ROWS FROM (semantic_trip_search(%s))"',
    'SQL = "SELECT (semantic_trip_search(%s)).package_id"',
    'SQL = "SELECT * FROM t CROSS JOIN LATERAL semantic_trip_search(%s)"',
    "from backend.retrieval.hybrid import SEMANTIC_SQL",
    "rows = execute(hybrid.SEMANTIC_SQL, (vector, 5))",
], ids=["plain", "lowercase and spaced", "schema", "line break and caps", "select list",
        "comma without space", "quoted", "schema quoted", "rows from",
        "select parenthesized", "lateral", "imported statement", "used statement"])
@pytest.mark.parametrize("where", [
    "backend/tools/other.py", "meridian_agentcore/agentcore/gateway_targets/meridian_holds/x.py",
    "meridian_agentcore/app/MeridianConcierge/reader.py",
])
def test_a_second_reader_of_the_vector_function_is_rejected(tmp_path, source, where):
    for name in VECTOR_FUNCTION_READERS:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("SQL = 'FROM semantic_trip_search(%s)'")
    for root in SCANNED_ROOTS:
        (tmp_path / root).mkdir(parents=True, exist_ok=True)
    assert vector_function_readers(tmp_path) == VECTOR_FUNCTION_READERS
    (tmp_path / where).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / where).write_text(source)
    assert vector_function_readers(tmp_path) == VECTOR_FUNCTION_READERS | {where}


def test_a_staged_copy_and_prose_about_the_function_are_not_readers(tmp_path):
    for root in SCANNED_ROOTS:
        (tmp_path / root).mkdir(parents=True, exist_ok=True)
    staged = tmp_path / "meridian_agentcore/app/MeridianWorkflow/backend/retrieval/hybrid.py"
    staged.parent.mkdir(parents=True)
    staged.write_text("SQL = 'FROM semantic_trip_search(%s)'")
    prose = tmp_path / "meridian_agentcore/app/MeridianConcierge/prompts.py"
    prose.parent.mkdir(parents=True, exist_ok=True)
    prose.write_text("PROMPT = 'call semantic_trip_search(query, limit=5) once'")
    assert vector_function_readers(tmp_path) == set()


async def test_a_duplicate_rerank_index_lists_its_trip_once(run):
    semantic = [row("A", similarity=0.9), row("B", similarity=0.8)]
    details = [row("A"), row("B")]
    rerank = [{"index": 1, "score": 0.97}, {"index": 1, "score": 0.5},
              {"index": 0, "score": 0.12}]

    ranked, _spans, counts = await run(FakeDb(semantic=semantic, details=details),
                                       FakeEmbeddings(rerank=rerank))

    assert [r["package_id"] for r in ranked] == ["B", "A"]
    assert [r["rank_delta"] for r in ranked] == [1, -1]
    assert counts["reranked"] is True
