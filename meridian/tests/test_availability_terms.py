"""A word in an availability question names a package only when it is long enough to mean it."""

import pytest

from backend.retrieval import availability

CATALOG = [
    {"package_id": "KYO-001", "name": "Kyoto Ryokan & Onsen", "operator": "Hoshino Travel"},
    {"package_id": "TKY-001", "name": "Tokyo Indie Walk", "operator": "Tokyo Walks"},
    {"package_id": "CTY-002", "name": "Tokyo Culture & Cuisine", "operator": "JAL Premium"},
]


class LikeDb:
    """Answers the name search the way PostgreSQL's ``LIKE`` would, over a small catalog."""

    def __init__(self):
        self.terms = []

    async def execute(self, sql, params=()):
        patterns = [p.strip("%") for p in params]
        self.terms = patterns[::2]
        return [
            row for row in CATALOG
            if any(p in row["name"].lower() or p in row["operator"].lower() for p in self.terms)
        ]


@pytest.mark.parametrize("query, expected", [
    ("is there a tour on friday", []),
    ("anything to book", []),
    ("is the tokyo indie walk available", ["TKY-001"]),
    ("check kyoto ryokan availability", ["KYO-001"]),
    ("what is the onsen package", ["KYO-001"]),
])
async def test_short_words_do_not_match_package_names(query, expected):
    db = LikeDb()

    rows = await availability._resolve_named_package(
        db, query.lower(), availability._search_terms(query.lower()))

    assert [row["package_id"] for row in rows][:1] == expected[:1]
    assert all(len(term) >= availability.MIN_TERM_LENGTH for term in db.terms)
    if not expected:
        assert rows == []


@pytest.mark.parametrize("query, terms", [
    ("is there a tour on friday", ["tour", "friday"]),
    ("Is Atlantis Deluxe available?", ["atlantis", "deluxe"]),
    ("tokyo to kyoto in 7 nights", ["tokyo", "kyoto", "nights"]),
    ("is the a an?", []),
])
def test_search_terms_drop_short_words_and_stopwords(query, terms):
    assert availability._search_terms(query.lower()) == terms
