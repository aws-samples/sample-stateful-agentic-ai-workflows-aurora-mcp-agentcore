"""Duration availability for named or listed trip packages."""

import re
from typing import Any, Dict, List, Optional

from backend.activity import ActivityEntry, Product, create_activity
from backend.catalog_compat import row_to_api_product
from backend.db.rds_data_client import get_rds_data_client
from backend.timing import clock, elapsed_ms


def _rank_named_package_matches(
    rows: List[Dict[str, Any]],
    query_lower: str,
    search_terms: List[str],
) -> List[Dict[str, Any]]:
    """Order candidate packages by how specifically the query names them.

    Ranking beats first-match here because the catalog has six Tokyo
    packages.  Matching on the first surviving token alone resolves "the
    Tokyo Executive Stopover" on "tokyo" and returns whichever row the
    planner emits first, which is neither correct nor stable between runs.

    Args:
        rows: Candidate package rows that matched at least one search term.
        query_lower: The lowercased traveler query.
        search_terms: Stopword-filtered tokens taken from the query.

    Returns:
        The rows, most specifically named first.  Ties break on package_id
        so repeated runs of the same query answer with the same package.
    """

    def score(row: Dict[str, Any]) -> tuple:
        name = (row.get("name") or "").lower()
        operator = (row.get("operator") or "").lower()
        name_words = [word for word in re.split(r"\W+", name) if word]
        # How much of the package name the traveler actually said, so a
        # two-of-three word match outranks two-of-six on a longer name.
        coverage = (
            sum(1 for word in name_words if word in search_terms) / len(name_words)
            if name_words
            else 0.0
        )
        return (
            1 if name and name in query_lower else 0,
            sum(1 for term in search_terms if term in name),
            round(coverage, 3),
            sum(1 for term in search_terms if term in operator),
        )

    return sorted(
        rows,
        key=lambda row: tuple(-value for value in score(row))
        + ((row.get("package_id") or ""),),
    )


async def _resolve_named_package(
    db: Any,
    query_lower: str,
    search_terms: List[str],
) -> List[Dict[str, Any]]:
    """Find the package a traveler named, ranked by specificity.

    Args:
        db: RDS Data API client.
        query_lower: The lowercased traveler query.
        search_terms: Stopword-filtered tokens taken from the query.

    Returns:
        Matching package rows, best match first; empty when nothing matched.
    """
    if not search_terms:
        return []

    # Bounded so a long sentence cannot build an unbounded OR chain.
    terms = search_terms[:12]
    clause = " OR ".join(
        ["(LOWER(name) LIKE %s OR LOWER(operator) LIKE %s)"] * len(terms)
    )
    params: List[str] = []
    for term in terms:
        params.extend([f"%{term}%", f"%{term}%"])

    rows = await db.execute(
        f"""
        SELECT package_id, name, operator, price_per_person, description,
               image_url, trip_type, destination, region, durations,
               availability, highlights
        FROM trip_packages
        WHERE {clause}
        """,
        tuple(params),
    )
    return _rank_named_package_matches(rows or [], query_lower, terms)


async def retrieval_availability_search(
    query: str,
    package_id: Optional[str] = None,
) -> tuple[List[Product], List[ActivityEntry], str]:
    """
    Phase 3: PackageAgent handles duration-inventory queries.
    Supervisor delegates availability questions to the specialist agent.

    Returns: (products, activities, message)
    """
    activities = []

    db = get_rds_data_client()

    activities.append(create_activity(
        activity_type="reasoning",
        title="Delegating to PackageAgent",
        details="Supervisor routing availability request to specialist agent",
        agent_name="RetrievalAgent",
        agent_file="agents/phase_03_retrieval/supervisor.py"
    ))

    query_lower = query.lower()

    exact_sql = """
        SELECT package_id, name, operator, price_per_person, description,
               image_url, trip_type, destination, region, durations,
               availability, highlights
        FROM trip_packages
        WHERE package_id = %s
        LIMIT 1
    """
    
    # Extract key terms from query
    search_terms = []
    stopwords = {
        'a', 'an', 'any', 'are', 'available', 'check', 'departures', 'do',
        'duration', 'durations', 'find', 'for', 'have', 'in', 'is', 'matching',
        'open', 'options', 'package', 'packages', 'plan', 'still', 'the', 'then',
        'trip', 'verify', 'what', 'which', 'you',
    }
    for raw_word in query_lower.split():
        # Trailing punctuation would survive into the LIKE pattern, and
        # "%tokyo?%" matches nothing.  Pure-punctuation tokens ("&") carry
        # no signal either.
        word = raw_word.strip(".,!?;:'\"()[]")
        if word and any(ch.isalnum() for ch in word) and word not in stopwords:
            search_terms.append(word)
    
    search_started = clock()
    if package_id:
        results = await db.execute(exact_sql, (package_id,))
    else:
        results = await _resolve_named_package(db, query_lower, search_terms)
    search_time = elapsed_ms(search_started)

    activities.append(create_activity(
        activity_type="search",
        title="PackageAgent: Finding package",
        details=(
            f"Loading ranked package {package_id}"
            if package_id
            else "Searching for mentioned trip package"
        ),
        execution_time_ms=search_time,
        agent_name="PackageAgent",
        agent_file="agents/phase_03_retrieval/package_agent.py"
    ))

    if not results:
        activities.append(create_activity(
            activity_type="result",
            title="PackageAgent: Package not found",
            agent_name="PackageAgent",
            agent_file="agents/phase_03_retrieval/package_agent.py"
        ))

        activities.append(create_activity(
            activity_type="result",
            title="PackageAgent returned to Supervisor",
            details="No matching package found",
            agent_name="RetrievalAgent",
            agent_file="agents/phase_03_retrieval/supervisor.py"
        ))

        return [], activities, "I couldn't find that trip package. Try searching by destination, operator, or trip type."

    product = results[0]
    availability = product.get('availability', {})

    activities.append(create_activity(
        activity_type="availability",
        title="PackageAgent: Checking duration inventory",
        details=f"Package: {product['name']}",
        sql_query="SELECT availability, durations FROM trip_packages WHERE package_id = ?",
        agent_name="PackageAgent",
        agent_file="agents/phase_03_retrieval/package_agent.py"
    ))
    
    # Calculate total stock
    if isinstance(availability, dict):
        if 'quantity' in availability:
            total_stock = availability['quantity']
        else:
            total_stock = sum(availability.values()) if availability else 0
    else:
        total_stock = 0
    
    # Totalled in memory from the row read above; there is no call to time.
    activities.append(create_activity(
        activity_type="result",
        title="PackageAgent: Duration inventory verified",
        details=f"Total: {total_stock} package places across available durations",
        agent_name="PackageAgent",
        agent_file="agents/phase_03_retrieval/package_agent.py"
    ))

    activities.append(create_activity(
        activity_type="result",
        title="PackageAgent returned to Supervisor",
        details=f"Availability check complete for {product['name']}",
        agent_name="RetrievalAgent",
        agent_file="agents/phase_03_retrieval/supervisor.py"
    ))

    durations = product.get('durations', [])
    if total_stock > 0:
        if durations:
            durations_str = ', '.join(durations[:5])
            message = (
                f"**{product['name']}** has {total_stock} package places "
                f"across these trip lengths: {durations_str}."
            )
        else:
            message = (
                f"**{product['name']}** has {total_stock} package places available."
            )
    else:
        message = f"**{product['name']}** is currently sold out. Would you like similar alternatives?"
    
    # Return the product
    products = [Product(**row_to_api_product(product))]
    
    return products, activities, message
