from datetime import datetime, timezone, date
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status

from ..config import settings

router = APIRouter(prefix="/api/v1", tags=["Collections"])

_collections_db: list[dict] = []
_B2B_SERVICE_HEADERS = {"X-Service-Key": settings.B2C_TO_B2B_KEY}


async def _fetch_b2b_batch(product_ids: list[str]) -> list[dict]:
    import httpx

    if not product_ids:
        return []

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.B2B_URL}/api/v1/public/products/batch",
            headers=_B2B_SERVICE_HEADERS,
            json={"product_ids": product_ids},
            timeout=10.0,
        )

    if resp.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service temporarily unavailable"},
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "B2B_ERROR", "message": f"B2B returned {resp.status_code}"},
        )

    return resp.json()


def _is_collection_active(col: dict, now_date: date) -> bool:
    if not col.get("is_active", False):
        return False
    start_date = col.get("start_date")
    if start_date:
        try:
            if isinstance(start_date, str):
                sd = date.fromisoformat(start_date)
            else:
                sd = start_date
            if sd > now_date:
                return False
        except (ValueError, TypeError):
            pass
    return True


@router.get("/main/collections", status_code=status.HTTP_200_OK)
async def list_collections(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    now = datetime.now(timezone.utc).date()
    active = [c for c in _collections_db if _is_collection_active(c, now)]
    active.sort(key=lambda c: c.get("priority", 0))

    total = len(active)
    page = active[offset: offset + limit]

    collections = [
        {
            "id": c["id"],
            "title": c["title"],
            "description": c.get("description", ""),
            "cover_image_url": c.get("cover_image_url"),
            "target_url": c.get("target_url"),
            "priority": c.get("priority", 0),
            "total_products": len(c.get("product_ids", [])),
        }
        for c in page
    ]

    return {
        "collections": collections,
        "metadata": {"total_count": total, "limit": limit, "offset": offset},
    }


@router.get("/collections/{collection_id}/products", status_code=status.HTTP_200_OK)
async def get_collection_products(
    collection_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    col = next((c for c in _collections_db if c["id"] == collection_id), None)
    if not col:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Collection not found"},
        )

    all_product_ids = col.get("product_ids", [])

    if not all_product_ids:
        return {
            "collection_id": col["id"],
            "collection_title": col["title"],
            "items": [],
            "unavailable_ids": [],
            "total_products": 0,
            "limit": limit,
            "offset": offset,
        }

    paginated_ids = all_product_ids[offset: offset + limit]

    try:
        b2b_products = await _fetch_b2b_batch(paginated_ids)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service temporarily unavailable"},
        )

    found_ids = {p["id"] for p in b2b_products}
    items = []
    for p in b2b_products:
        skus = p.get("skus", [])
        images = p.get("images", [])
        items.append({
            "id": p["id"],
            "title": p.get("title", ""),
            "slug": p.get("slug"),
            "min_price": skus[0]["price"] if skus else 0,
            "old_price": skus[0].get("old_price") if skus else None,
            "has_stock": any(s.get("active_quantity", 0) > 0 for s in skus),
            "rating": p.get("rating"),
            "reviews_count": p.get("reviews_count", 0),
            "images": [
                {
                    "id": img.get("id", ""),
                    "url": img.get("url", ""),
                    "alt": img.get("alt", ""),
                    "ordering": img.get("ordering", 0),
                    "is_main": img.get("is_main", False),
                }
                for img in images
            ],
            "seller": p.get("seller"),
        })

    unavailable_ids = [pid for pid in paginated_ids if pid not in found_ids]

    return {
        "collection_id": col["id"],
        "collection_title": col["title"],
        "items": items,
        "unavailable_ids": unavailable_ids,
        "total_products": len(all_product_ids),
        "limit": limit,
        "offset": offset,
    }
