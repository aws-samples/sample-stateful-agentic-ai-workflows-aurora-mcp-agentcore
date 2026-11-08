"""Hard exclusions apply to semantic candidates against PostgreSQL's actual text search."""

from psycopg.rows import dict_row
import pytest

from backend.retrieval import hybrid


class LocalSearch:
    def __init__(self, connection):
        self.connection = connection

    async def execute(self, statement, parameters=()):
        with self.connection.cursor(row_factory=dict_row) as cursor:
            return cursor.execute(statement, parameters).fetchall()


class Ranking:
    def generate_text_embedding(self, *_args, **_kwargs):
        return [1.0] * 1024

    def rerank_documents(self, _query, documents, top_n):
        return [{"index": i, "score": 1.0} for i in range(min(len(documents), top_n))]


@pytest.mark.parametrize(
    "query,excluded",
    [
        ("Tokyo -luxury", "Tokyo luxury"),
        ('Tokyo -"private pool"', "Tokyo private pool"),
        ("Tokyo -luxury -resort", "Tokyo resort"),
    ],
)
async def test_exclusions_survive_semantic_union(booking_db, monkeypatch, query, excluded):
    vector = "[" + ",".join(["1"] * 1024) + "]"
    with booking_db.connect() as connection:
        connection.execute("UPDATE trip_packages SET embedding = %s::vector", (vector,))
        connection.execute(
            "INSERT INTO trip_packages(package_id, name, trip_type, destination, "
            "price_per_person, durations, availability, embedding) "
            "VALUES ('excluded', %s, 'City Breaks', 'Tokyo', 100, '[\"2 nights\"]', "
            "'{\"2 nights\": 1}', %s::vector)",
            (excluded, vector),
        )
        monkeypatch.setattr(hybrid, "get_rds_data_client", lambda: LocalSearch(connection))
        monkeypatch.setattr(hybrid, "get_embedding_service", Ranking)
        rows, _spans, counts = await hybrid.retrieval_rows(query)
        assert counts["semantic"] == 2
        assert [row["package_id"] for row in rows] == ["pkg"]
