"""Budget cases that both ceiling implementations must answer identically.

The backend (``backend/agents/budget.py``) and the holds Lambda cannot import each other,
so ``tests/test_budget_parity.py`` runs this one table against both. ``UNCLEAR`` means the
first budget fact present is not one dollar amount or one range: the ceiling is zero and the
governed write refuses by name. ``DEFAULT`` means no budget fact at all.
"""

from __future__ import annotations

UNCLEAR = "unclear"
DEFAULT = "default"
DEFAULT_CENTS = 400000

JORDAN = [
    {"key": "budget_cap", "value": "$3,200"},
    {"key": "per_person_range", "value": "Prefers $2k-3.5k per person"},
]
SMALL_CAP = {"key": "budget_cap", "value": "$75"}
RANGE = {"key": "per_person_range", "value": "$100 to $200"}


def _fact(value, key="budget_cap"):
    return [{"key": key, "value": value}]


# (case id, facts, travelers, per-traveler cents (party total is that times travelers), UNCLEAR
# or DEFAULT)
CEILING_CASES = [
    ("small_cap_one_traveler", [SMALL_CAP], 1, 7500),
    ("small_cap_two_travelers", [SMALL_CAP], 2, 7500),
    ("small_cap_beats_the_secondary_range", [SMALL_CAP, RANGE], 1, 7500),
    ("small_cap_beats_the_secondary_range_for_two", [SMALL_CAP, RANGE], 2, 7500),
    ("range_listed_first_still_loses_to_the_cap", [RANGE, SMALL_CAP], 2, 7500),
    ("control_125", _fact("$125"), 1, 12500),
    ("jordan_seeded_one", JORDAN, 1, 320000),
    ("jordan_seeded_two", JORDAN, 2, 320000),
    ("range_alone_takes_the_upper_bound", _fact("Prefers $2k-3.5k per person", "per_person_range"),
     2, 350000),
    ("k_suffix_is_thousands", _fact("$2k"), 1, 200000),
    ("us_dollar_prefix", _fact("US$75"), 1, 7500),
    ("usd_suffix", _fact("75 USD"), 1, 7500),
    ("usd_suffix_with_k", _fact("2k USD"), 1, 200000),
    ("thousands_separator", _fact("$3,200"), 1, 320000),
    ("cents_are_kept", _fact("$90 to $2,750.50", "budget_range"), 1, 275050),
    ("later_figure_inherits_the_dollar_sign", _fact("$100 to 200"), 1, 20000),
    ("later_figure_inherits_after_a_dash", _fact("$75-150"), 1, 15000),
    ("words_around_the_amount", _fact("Max $3,500 per person"), 2, 350000),
    ("a_party_size_is_not_an_amount", _fact("$75 for 3 kids"), 1, 7500),
    ("the_budget_key_beats_the_range_key",
     [{"key": "per_person_range", "value": "$100 to $200"}, {"key": "budget", "value": "$75"}],
     1, 7500),
    ("the_first_row_of_a_key_wins",
     [{"key": "budget_cap", "value": "$3,000"}, {"key": "budget_cap", "value": "$1,000"}],
     1, 300000),
    ("key_case_is_ignored", [{"key": "Budget", "value": "$1.5k"}], 1, 150000),
    ("no_facts", [], 2, DEFAULT),
    ("no_budget_fact", [{"key": "home_airport", "value": "JFK"}], 2, DEFAULT),
    ("a_single_bare_number_counts_as_dollars", _fact("3500"), 2, 350000),
    ("a_single_bare_k_counts_as_dollars", _fact("around 1.5k", "budget"), 1, 150000),
    ("a_bare_number_with_words_counts_as_dollars", _fact("3000 per person"), 2, 300000),
    ("a_bare_joined_range_takes_the_upper_bound", _fact("2000-3500"), 2, 350000),
    ("a_bare_range_with_to", _fact("2000 to 3500"), 1, 350000),
    ("usd_before_the_number_without_a_space", _fact("USD3000"), 1, 300000),
    ("usd_before_the_number_with_a_space", _fact("USD 3000"), 1, 300000),
    ("a_marked_range_with_usd_on_both_figures", _fact("USD2000-USD3500"), 1, 350000),
    ("an_earlier_bare_figure_joins_a_marked_one", _fact("2k to $3.5k"), 1, 350000),
    ("two_marked_amounts_are_unclear", _fact("$75 per traveler, $3200 total"), 1, UNCLEAR),
    ("an_either_or_is_unclear", _fact("$75 or $5000"), 1, UNCLEAR),
    ("two_bare_numbers_are_unclear", _fact("75 for 3 kids"), 1, UNCLEAR),
    ("three_figures_in_a_chain_are_unclear", _fact("$1000-2000-3000"), 1, UNCLEAR),
    ("a_zero_figure_in_a_range_is_unclear", _fact("$0 to $500"), 1, UNCLEAR),
    ("bare_zero_is_unclear", _fact("0"), 1, UNCLEAR),
    ("words_only", _fact("flexible", "budget"), 3, UNCLEAR),
    ("empty_value", _fact(""), 1, UNCLEAR),
    ("zero_is_not_an_amount", _fact("$0"), 1, UNCLEAR),
    ("euro_symbol", _fact("€500"), 1, UNCLEAR),
    ("euro_code", _fact("500 EUR"), 1, UNCLEAR),
    ("canadian_dollar_prefix", _fact("C$75"), 1, UNCLEAR),
    ("mixed_currencies", _fact("$75 or €80"), 1, UNCLEAR),
    ("unclear_cap_never_falls_to_a_lower_key",
     [{"key": "budget_cap", "value": "no limit"}, {"key": "budget", "value": "$4,000"}],
     1, UNCLEAR),
    ("unclear_cap_never_falls_to_the_range",
     [{"key": "budget_cap", "value": "flexible"}, {"key": "per_person_range", "value": "$2k-3k"}],
     2, UNCLEAR),
    ("unclear_cap_never_falls_to_the_default", _fact("a few hundred"), 1, UNCLEAR),
]

# (text, amounts in dollars, or None when not unambiguous USD)
AMOUNT_CASES = [
    ("$75", [75.0]),
    ("$3,200", [3200.0]),
    ("$2k", [2000.0]),
    ("$2K", [2000.0]),
    ("US$75", [75.0]),
    ("US $75", [75.0]),
    ("75 USD", [75.0]),
    ("75usd", [75.0]),
    ("$2k-3.5k", [2000.0, 3500.0]),
    ("$100 to $200", [100.0, 200.0]),
    ("$100 to 200", [100.0, 200.0]),
    ("Prefers $2k-3.5k per person", [2000.0, 3500.0]),
    ("under $1500, 3 kids", [1500.0]),
    ("200 keep it low", [200.0]),
    ("75", [75.0]),
    ("3000 per person", [3000.0]),
    ("2000-3500", [2000.0, 3500.0]),
    ("2000 to 3500", [2000.0, 3500.0]),
    ("USD3000", [3000.0]),
    ("USD 3000", [3000.0]),
    ("usd2k", [2000.0]),
    ("2k to $3.5k", [2000.0, 3500.0]),
    ("$75 for 3 kids", [75.0]),
    ("$75 per traveler, $3200 total", None),
    ("$75 or $5000", None),
    ("75 for 3 kids", None),
    ("$1000-2000-3000", None),
    ("$0 to $500", None),
    ("0", None),
    ("", None),
    ("A$75", None),
    ("£75", None),
    ("$75 / 80 CAD", None),
]
