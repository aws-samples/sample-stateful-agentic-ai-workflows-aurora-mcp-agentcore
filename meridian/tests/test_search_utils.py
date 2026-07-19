"""Unit tests for ``backend.search_utils.parse_search_query``."""

from __future__ import annotations

from backend.search_utils import parse_search_query


class TestParseSearchQueryPriceExtraction:
    def test_extracts_under_price(self):
        params = parse_search_query("Beach trips under $1500")
        assert params.price_filter == 1500.0

    def test_extracts_below_price_decimals(self):
        params = parse_search_query("luxury below $2999.99")
        assert params.price_filter == 2999.99

    def test_extracts_price_with_thousands_separator(self):
        params = parse_search_query("city trips under $2,000")
        assert params.price_filter == 2000.0

    def test_extracts_less_than_price(self):
        params = parse_search_query("city break less than 1200")
        assert params.price_filter == 1200.0

    def test_extracts_lt_symbol_price(self):
        params = parse_search_query("adventure < $800")
        assert params.price_filter == 800.0

    def test_no_price_returns_none(self):
        params = parse_search_query("Tokyo culture trip")
        assert params.price_filter is None


class TestParseSearchQueryCategoryRouting:
    def test_city_break_singular(self):
        params = parse_search_query("show me a city break")
        assert params.matched_trip_type == "City Breaks"

    def test_canonical_city_trip_prompt(self):
        params = parse_search_query(
            "Show me city trips under $2,000 per traveler."
        )
        assert params.matched_trip_type == "City Breaks"
        assert params.price_filter == 2000.0

    def test_canonical_beach_and_resort_prompt(self):
        params = parse_search_query(
            "Show me beach and resort trips under $2,500 per traveler."
        )
        assert params.matched_trip_type == "Beach & Resort"
        assert params.price_filter == 2500.0

    def test_beach_keyword(self):
        params = parse_search_query("beach trips in Greece")
        assert params.matched_trip_type == "Beach & Resort"

    def test_resort_keyword(self):
        params = parse_search_query("all-inclusive resort options")
        assert params.matched_trip_type == "Beach & Resort"

    def test_adventure_keyword(self):
        params = parse_search_query("adventure hiking week")
        assert params.matched_trip_type == "Adventure & Outdoors"

    def test_wellness_keyword(self):
        params = parse_search_query("spa wellness retreat")
        assert params.matched_trip_type == "Wellness & Luxury"

    def test_no_category_match_returns_none(self):
        # A purely semantic prompt should NOT short-circuit into a SQL category.
        # That's the whole point of Phase 3+ retrieval.
        params = parse_search_query("a slow week somewhere we can drink good wine")
        assert params.matched_trip_type is None


class TestParseSearchQueryShape:
    def test_lowercases_input_and_builds_like_pattern(self):
        params = parse_search_query("Tokyo")
        assert params.query == "Tokyo"
        assert params.query_lower == "tokyo"
        assert params.search_pattern == "%Tokyo%"

    def test_handles_unicode_input_safely(self):
        params = parse_search_query("Côte d'Azur retreat")
        assert "côte" in params.query_lower
        assert params.search_pattern == "%Côte d'Azur retreat%"
