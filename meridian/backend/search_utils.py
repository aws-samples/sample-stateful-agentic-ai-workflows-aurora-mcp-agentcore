"""
Shared keyword search utilities for trip_packages (Phases 1 & 2).
"""

import re
import textwrap
from typing import Any, Dict, List, Optional, Sequence
from dataclasses import dataclass

from backend.config import config

PACKAGE_COLUMNS = """package_id, name, trip_type, destination, region,
                     price_per_person, operator, description, image_url,
                     durations, package_places_left(package_id) AS availability, highlights"""

# "under $2,500.00", "below $3.5k", "less than 1200", "< $800". The whole part is either
# comma-grouped thousands or plain digits; "k" multiplies by a thousand.
PRICE_CEILING = re.compile(
    r"(?:under|below|less than|<)\s*(\$)?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?:\s*(k)\b)?",
    re.IGNORECASE,
)
# Without "$" or "k", a small number is a count ("under 3 nights"), not a budget.
MIN_PLAIN_PRICE = 100


def parse_price_ceiling(query: str) -> Optional[float]:
    """Return the budget ceiling a prompt names, in dollars, or None.

    Args:
        query: The traveler's words.

    Returns:
        The first amount after "under", "below", "less than" or "<" that reads as
        money: one with "$" or a "k" suffix, or a plain number of at least
        ``MIN_PLAIN_PRICE``. None when the prompt names no such amount.
    """
    for match in PRICE_CEILING.finditer(query):
        dollar, whole, fraction, thousands = match.groups()
        value = float(whole.replace(",", "") + (fraction or ""))
        if thousands:
            return value * 1000
        if dollar or value >= MIN_PLAIN_PRICE:
            return value
    return None


@dataclass
class SearchParams:
    query: str
    query_lower: str
    price_filter: Optional[float]
    matched_trip_type: Optional[str]


_PERCENT_S = re.compile(r"%s")
_NUMBERED = re.compile(r"\$(\d+)")
_OTHER_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x85]")
MAX_SHOWN_CHARS = 60
SHOWN_PREFIX_CHARS = 40


def _tidy(sql: str) -> str:
    """Collapse whitespace, except in text with ``--`` comments, which need their lines."""
    return textwrap.dedent(sql).strip() if "--" in sql else " ".join(sql.split())


def numbered_sql(sql: str) -> str:
    """The statement with each ``%s`` replaced by ``$1``, ``$2`` in order of appearance."""
    counter = iter(range(1, 10_000))
    return _PERCENT_S.sub(lambda _match: f"${next(counter)}", _tidy(sql))


def _shown(value: Any) -> str:
    """One bound value as it reads in a trace: quoted text, bare numbers, no floods."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if len(text) > MAX_SHOWN_CHARS:
        text = f"{text[:SHOWN_PREFIX_CHARS]}... ({len(text)} characters)"
    escaped = (
        text.replace("\\", "\\\\")
        .replace("'", "''")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )
    escaped = _OTHER_CONTROL_CHARACTER.sub(lambda match: f"\\x{ord(match.group()):02x}", escaped)
    return f"'{escaped}'"


def display_sql(sql: str, params: Sequence[Any] = ()) -> str:
    """Show a statement as it ran: ``$n`` placeholders, then the values bound to them.

    Args:
        sql: The statement, with ``%s`` placeholders (as executed) or ``$n`` ones.
        params: The values bound to the placeholders, in order.

    Returns:
        The numbered statement, and when ``params`` is not empty a last line
        ``-- Values: $1 = ..., $2 = ...``.

    Raises:
        ValueError: The number of placeholders and values disagree. A trace that
            shows values the statement never bound would be a false record.
    """
    statement = numbered_sql(sql)
    highest = max((int(number) for number in _NUMBERED.findall(statement)), default=0)
    if highest != len(params):
        raise ValueError(
            f"display_sql: the statement binds {highest} parameter(s) but {len(params)} "
            f"value(s) were given: {statement[:80]}"
        )
    if not params:
        return statement
    values = ", ".join(f"${index} = {_shown(value)}" for index, value in enumerate(params, 1))
    return f"{statement}\n-- Values: {values}"


def search_title(params: SearchParams) -> str:
    """The span title for a keyword search: the trip type it filtered on, or the text."""
    if params.matched_trip_type:
        return f"Trip type filter: {params.matched_trip_type}"
    return f"Filter search: {params.query}"


def parse_search_query(query: str) -> SearchParams:
    query_lower = query.lower()
    price_filter = parse_price_ceiling(query)

    matched_trip_type = None
    for keyword, trip_type in config.search.category_keywords.items():
        if keyword in query_lower and trip_type:
            matched_trip_type = trip_type
            break

    return SearchParams(
        query=query,
        query_lower=query_lower,
        price_filter=price_filter,
        matched_trip_type=matched_trip_type,
    )


def results_to_packages(results: List[Dict]) -> List[Dict]:
    return [
        {
            "package_id": row["package_id"],
            "name": row["name"],
            "trip_type": row["trip_type"],
            "destination": row.get("destination") or "",
            "region": row.get("region") or "",
            "price_per_person": float(row["price_per_person"]),
            "operator": row.get("operator") or "",
            "description": row.get("description") or "",
            "image_url": row.get("image_url") or "",
            "durations": row.get("durations"),
            "availability": row.get("availability"),
            "highlights": row.get("highlights"),
        }
        for row in results
    ]


# Back-compat alias used during migration
results_to_products = results_to_packages
