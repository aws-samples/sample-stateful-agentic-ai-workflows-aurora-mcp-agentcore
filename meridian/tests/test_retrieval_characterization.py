"""Retrieval output is pinned while the moved functions are split.

Only boundaries are replaced: the Data API client, the embedding service and the
clock. The golden file is written once from the code as A1 moved it, by running
this module with MERIDIAN_WRITE_GOLDEN=1, and must not change afterwards.
"""

import json
import os
from pathlib import Path

import pytest

from backend.retrieval import availability, hybrid

GOLDEN = Path(__file__).parent / "fixtures" / "retrieval_golden.json"
VOLATILE = {"id", "timestamp"}

ROW = {
    "package_id": "PKG-TYO-1", "name": "Tokyo Executive Stopover", "operator": "JAL Premium",
    "price_per_person": 1949.0, "description": "Marunouchi business hotel",
    "image_url": "https://example.test/tokyo.jpg", "trip_type": "city", "destination": "Tokyo",
    "region": "Asia", "durations": ["2 nights", "4 nights"],
    "availability": {"2 nights": 3, "4 nights": 0}, "highlights": ["Haneda lounge"],
    "similarity": 0.91, "semantic_score": 0.91,
}
SECOND = {**ROW, "package_id": "PKG-TYO-2", "name": "Kyoto Rail Extension", "similarity": 0.84,
          "semantic_score": 0.84}
SOLD_OUT = {**ROW, "availability": {"2 nights": 0, "4 nights": 0}}
QUANTITY = {**ROW, "availability": {"quantity": 7}, "durations": []}
NO_DURATIONS = {**ROW, "durations": []}


class RecordingDb:
    """Answers each query by its real SQL shape and records what was asked."""

    def __init__(self, *, exact_rows=None, named_rows=None):
        self.calls = []
        self._exact_rows = [ROW] if exact_rows is None else exact_rows
        self._named_rows = [ROW] if named_rows is None else named_rows

    async def execute(self, sql, params=None, transaction_id=None):
        self.calls.append({"sql": " ".join(sql.split()), "params": list(params or ())})
        if "semantic_trip_search" in sql:
            return [ROW, SECOND]
        if "ts_rank" in sql:
            return [SECOND]
        if "WHERE package_id = %s" in sql:
            return list(self._exact_rows)
        return list(self._named_rows)


class FixedEmbeddings:
    def generate_text_embedding(self, text):
        return [0.25] * 1024

    def rerank_documents(self, query, docs, top_n=5):
        return [{"index": 1}, {"index": 0}][:top_n]


class FailingRerank(FixedEmbeddings):
    def rerank_documents(self, query, docs, top_n=5):
        raise RuntimeError("rerank offline")


def _stable(value):
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in sorted(value.items())
                if k not in VOLATILE and not k.endswith("_ms")}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def _dump(products, activities, db, message=None):
    return _stable({
        "products": [p.model_dump() for p in products],
        "activities": [a.model_dump() for a in activities],
        "calls": db.calls,
        "message": message,
    })


CASES = {
    "hybrid_ranked": ("search", "Tokyo city trips under $2000", FixedEmbeddings, {}),
    "hybrid_price_filter": ("search", "Tokyo city trips under $1900", FixedEmbeddings, {}),
    "hybrid_rerank_down": ("search", "Kyoto rail", FailingRerank, {}),
    "availability_by_id": ("availability", "Is it available?", "PKG-TYO-1", {}),
    "availability_by_name": (
        "availability", "Is Tokyo Executive Stopover available?", None, {}
    ),
    "availability_not_found_by_id": (
        "availability", "Is it available?", "MISSING", {"exact_rows": []}
    ),
    "availability_not_found_by_name": (
        "availability", "Is Atlantis Deluxe available?", None, {"named_rows": []}
    ),
    "availability_no_terms": ("availability", "is the a an?", None, {}),
    "availability_sold_out": (
        "availability", "Is it available?", "PKG-TYO-1", {"exact_rows": [SOLD_OUT]}
    ),
    "availability_quantity": (
        "availability", "Is it available?", "PKG-TYO-1", {"exact_rows": [QUANTITY]}
    ),
    "availability_no_durations": (
        "availability", "Is it available?", "PKG-TYO-1", {"exact_rows": [NO_DURATIONS]}
    ),
}


async def _run(case, monkeypatch):
    kind, query, extra, db_rows = CASES[case]
    db = RecordingDb(**db_rows)
    if kind == "search":
        monkeypatch.setattr(hybrid, "get_rds_data_client", lambda: db)
        monkeypatch.setattr(hybrid, "get_embedding_service", lambda: extra())
        products, activities = await hybrid.retrieval_search(query, limit=2)
        return _dump(products, activities, db)
    monkeypatch.setattr(availability, "get_rds_data_client", lambda: db)
    products, activities, message = await availability.retrieval_availability_search(
        query, package_id=extra
    )
    return _dump(products, activities, db, message)


@pytest.mark.parametrize("case", sorted(CASES))
async def test_retrieval_output_is_unchanged(case, monkeypatch):
    observed = await _run(case, monkeypatch)
    if os.getenv("MERIDIAN_WRITE_GOLDEN") == "1":
        golden = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
        golden[case] = observed
        GOLDEN.write_text(json.dumps(golden, indent=2, sort_keys=True) + "\n")
    assert observed == json.loads(GOLDEN.read_text())[case]
