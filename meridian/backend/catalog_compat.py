"""Map trip_packages rows to legacy API Product shape (frontend compat)."""

from typing import Any, Dict, List, Optional


def row_to_api_product(row: Dict[str, Any]) -> Dict[str, Any]:
    sim = row.get("combined_score", row.get("semantic_score", row.get("similarity")))
    return {
        "product_id": row["package_id"],
        "name": row["name"],
        "brand": row.get("operator") or "",
        "price": float(row.get("price_per_person", row.get("price", 0))),
        "description": row.get("description") or "",
        "image_url": row.get("image_url") or "",
        "category": row.get("trip_type", row.get("category", "")),
        "destination": row.get("destination") or "",
        "region": row.get("region") or "",
        "available_sizes": row.get("durations", row.get("available_sizes")),
        "availability": row.get("availability"),
        "highlights": row.get("highlights"),
        "similarity": float(sim) if sim is not None else None,
    }


def rows_to_api_products(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [row_to_api_product(r) for r in rows]
