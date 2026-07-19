"""Trip package catalog API (trip_packages table)."""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.db.rds_data_client import get_rds_data_client

router = APIRouter(prefix="/api/packages", tags=["packages"])

# Legacy path alias
legacy_router = APIRouter(prefix="/api/products", tags=["products"])


class TripPackage(BaseModel):
    package_id: str
    name: str
    trip_type: str
    destination: str = ""
    region: str = ""
    price_per_person: float
    operator: str = ""
    description: str = ""
    image_url: str = ""
    durations: Optional[List[str]] = None
    availability: Optional[dict] = None
    highlights: Optional[List[str]] = None
    similarity: Optional[float] = None


class PackageListResponse(BaseModel):
    packages: List[TripPackage]
    total: int


def _row_to_package(row: dict, similarity: Optional[float] = None) -> TripPackage:
    return TripPackage(
        package_id=row["package_id"],
        name=row["name"],
        trip_type=row["trip_type"],
        destination=row.get("destination") or "",
        region=row.get("region") or "",
        price_per_person=float(row["price_per_person"]),
        operator=row.get("operator") or "",
        description=row.get("description") or "",
        image_url=row.get("image_url") or "",
        durations=row.get("durations"),
        availability=row.get("availability"),
        highlights=row.get("highlights"),
        similarity=similarity,
    )


async def _list_packages(
    trip_type: Optional[str],
    limit: int,
    offset: int,
    featured: bool,
) -> PackageListResponse:
    db = get_rds_data_client()
    cols = """package_id, name, trip_type, destination, region, price_per_person,
              operator, description, image_url, durations, availability, highlights"""

    if featured:
        query = f"""
            WITH ranked AS (
                SELECT {cols},
                       ROW_NUMBER() OVER (PARTITION BY trip_type ORDER BY price_per_person DESC) AS rn
                FROM trip_packages
            )
            SELECT {cols}
            FROM ranked
            WHERE rn <= 2
            ORDER BY trip_type, price_per_person DESC
            LIMIT %s
        """
        results = await db.execute(query, (min(limit, 10),))
        packages = [_row_to_package(r) for r in results]
        return PackageListResponse(packages=packages, total=len(packages))

    if trip_type:
        query = f"""
            SELECT {cols} FROM trip_packages
            WHERE trip_type = %s
            ORDER BY name LIMIT %s OFFSET %s
        """
        results = await db.execute(query, (trip_type, limit, offset))
        count = await db.execute_one(
            "SELECT COUNT(*) AS count FROM trip_packages WHERE trip_type = %s", (trip_type,)
        )
    else:
        query = f"""
            SELECT {cols} FROM trip_packages
            ORDER BY name LIMIT %s OFFSET %s
        """
        results = await db.execute(query, (limit, offset))
        count = await db.execute_one("SELECT COUNT(*) AS count FROM trip_packages")

    total = int(count["count"]) if count else 0
    return PackageListResponse(
        packages=[_row_to_package(r) for r in results],
        total=total,
    )


async def _get_package(package_id: str) -> TripPackage:
    db = get_rds_data_client()
    row = await db.execute_one(
        """
        SELECT package_id, name, trip_type, destination, region, price_per_person,
               operator, description, image_url, durations, availability, highlights
        FROM trip_packages WHERE package_id = %s
        """,
        (package_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Package {package_id} not found")
    return _row_to_package(row)


@router.get("", response_model=PackageListResponse)
async def list_packages(
    trip_type: Optional[str] = Query(None, alias="category"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    featured: bool = Query(False),
) -> PackageListResponse:
    try:
        return await _list_packages(trip_type, limit, offset, featured)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {e}") from e


@router.get("/{package_id}", response_model=TripPackage)
async def get_package(package_id: str) -> TripPackage:
    try:
        return await _get_package(package_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {e}") from e


# --- Legacy /api/products responses (maps to packages) ---

class LegacyProduct(BaseModel):
    product_id: str = Field(validation_alias="package_id")
    name: str
    category: str = Field(validation_alias="trip_type")
    brand: str = Field(validation_alias="operator")
    price: float = Field(validation_alias="price_per_person")
    description: str
    image_url: str
    destination: Optional[str] = None
    region: Optional[str] = None
    available_sizes: Optional[List[str]] = Field(default=None, validation_alias="durations")
    availability: Optional[dict] = None
    highlights: Optional[List[str]] = None
    similarity: Optional[float] = None

    model_config = {"populate_by_name": True}


@legacy_router.get("")
async def legacy_list(
    category: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    featured: bool = False,
):
    data = await _list_packages(category, limit, offset, featured)
    return {
        "products": [
            {
                "product_id": p.package_id,
                "name": p.name,
                "category": p.trip_type,
                "brand": p.operator,
                "price": p.price_per_person,
                "description": p.description,
                "image_url": p.image_url,
                "destination": p.destination,
                "region": p.region,
                "available_sizes": p.durations,
                "availability": p.availability,
                "highlights": p.highlights,
                "similarity": p.similarity,
            }
            for p in data.packages
        ],
        "total": data.total,
    }


@legacy_router.get("/{product_id}")
async def legacy_get(product_id: str):
    p = await _get_package(product_id)
    return {
        "product_id": p.package_id,
        "name": p.name,
        "category": p.trip_type,
        "brand": p.operator,
        "price": p.price_per_person,
        "description": p.description,
        "image_url": p.image_url,
        "destination": p.destination,
        "region": p.region,
        "available_sizes": p.durations,
        "availability": p.availability,
        "highlights": p.highlights,
    }
