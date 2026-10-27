"""A budget named in a search prompt becomes the price ceiling the traveler meant.

Both keyword search (Phases 1 and 2) and hybrid retrieval (Phases 3 and 5) filter
candidates by this ceiling, so a misread amount silently drops suitable trips.
"""

import pytest

from backend.retrieval import hybrid
from backend.search_utils import parse_price_ceiling, parse_search_query


@pytest.mark.parametrize(
    ("query", "ceiling"),
    [
        ("Tokyo trips under $2,000", 2000.0),
        ("Tokyo trips under $2,500.00", 2500.0),
        ("Tokyo trips under $3.5k", 3500.0),
        ("Tokyo trips under $3K per person", 3000.0),
        ("Tokyo trips under 4k", 4000.0),
        ("Tokyo trips under $3200", 3200.0),
        ("Tokyo trips under $12,500", 12500.0),
        ("luxury below $2999.99", 2999.99),
        ("city break less than 1200", 1200.0),
        ("adventure < $800", 800.0),
        ("Tokyo trips UNDER $2,000", 2000.0),
        ("Tokyo trips under $ 2,000", 2000.0),
    ],
)
def test_supported_money_formats_parse_to_the_intended_ceiling(query, ceiling):
    assert parse_price_ceiling(query) == ceiling


@pytest.mark.parametrize(
    "query",
    [
        "Tokyo culture trip",
        "Tokyo trips under 3 nights",
        "a trip for under 5 kids",
        "under $",
    ],
)
def test_no_ceiling_without_an_unambiguous_amount(query):
    assert parse_price_ceiling(query) is None


def test_a_later_amount_is_used_when_an_earlier_number_is_not_a_price():
    assert parse_price_ceiling("less than 3 nights, under $2,000") == 2000.0


def test_keyword_search_reads_the_same_ceiling():
    assert parse_search_query("Show me city trips under $3.5k per traveler.").price_filter == 3500.0


def _row(package_id, price, places=3):
    return {
        "package_id": package_id, "name": package_id.title(), "price_per_person": price,
        "operator": "Costa", "description": "d", "image_url": "", "trip_type": "Beach",
        "destination": "Japan", "region": "Asia", "durations": ["7 nights"],
        "availability": {"7 nights": places}, "highlights": [],
    }


class _Db:
    def __init__(self):
        self.lexical_params = None

    async def execute(self, sql, params=None, transaction_id=None):
        if "semantic_trip_search" in sql:
            return [_row("CHEAP", 1900.0), _row("MID", 2400.0), _row("DEAR", 2600.0)]
        if "ts_rank" in sql:
            self.lexical_params = list(params or ())
            return []
        return [_row("CHEAP", 1900.0), _row("MID", 2400.0), _row("DEAR", 2600.0)]


class _Embeddings:
    def generate_text_embedding(self, text, input_type="search_document"):
        return [0.0] * 1024

    def rerank_documents(self, query, docs, top_n=5):
        return [{"index": index} for index in range(len(docs))][:top_n]


async def test_hybrid_retrieval_keeps_trips_within_a_comma_formatted_budget(monkeypatch):
    db = _Db()
    monkeypatch.setattr(hybrid, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(hybrid, "get_embedding_service", _Embeddings)
    products, spans = await hybrid.retrieval_search("Japan trips under $2,500.00", limit=5)
    assert [product.product_id for product in products] == ["CHEAP", "MID"]
    gate = next(span for span in spans if span.title == "Eligibility filter applied")
    assert "over the $2,500 budget" in gate.details
    assert db.lexical_params[-1] == 25
