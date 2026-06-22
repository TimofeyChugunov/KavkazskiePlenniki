from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from ..config import settings

router = APIRouter(prefix="/api/v1", tags=["Favorites"])

_favorites_db: dict[str, list[dict]] = {}


def _get_user_id(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return user_id
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        from jose import jwt, JWTError

        try:
            payload = jwt.decode(
                auth[7:],
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
            )
            sub = payload.get("sub")
            if sub:
                return sub
        except JWTError:
            pass
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "UNAUTHORIZED", "message": "Invalid or missing token"},
    )


def _get_user_favorites(user_id: str) -> list[dict]:
    return _favorites_db.setdefault(user_id, [])


async def _enrich_from_b2b(product_ids: list[str]) -> dict[str, dict]:
    import httpx

    if not product_ids:
        return {}

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.B2B_URL}/api/v1/public/products",
            headers={"X-Service-Key": settings.B2C_TO_B2B_KEY},
            params={"ids": ",".join(product_ids), "limit": 100},
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

    data = resp.json()
    return {p["id"]: p for p in data.get("items", [])}


@router.post("/favorites/{product_id}")
async def add_to_favorites(product_id: str, request: Request):
    user_id = _get_user_id(request)
    favorites = _get_user_favorites(user_id)

    existing = next((f for f in favorites if f["product_id"] == product_id), None)
    if existing:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"product_id": existing["product_id"], "added_at": existing["added_at"]},
        )

    added_at = datetime.now(timezone.utc).isoformat()
    favorites.append({"product_id": product_id, "added_at": added_at})
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"product_id": product_id, "added_at": added_at},
    )


@router.delete("/favorites/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_favorites(product_id: str, request: Request):
    user_id = _get_user_id(request)
    favorites = _get_user_favorites(user_id)
    _favorites_db[user_id] = [f for f in favorites if f["product_id"] != product_id]


@router.get("/favorites", status_code=status.HTTP_200_OK)
async def get_favorites(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    user_id = _get_user_id(request)
    favorites = _get_user_favorites(user_id)

    total = len(favorites)
    page = favorites[offset: offset + limit]

    if not page:
        return {"items": [], "total_count": total, "limit": limit, "offset": offset}

    product_ids = [f["product_id"] for f in page]
    try:
        enriched = await _enrich_from_b2b(product_ids)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service temporarily unavailable"},
        )

    items = []
    for fav in page:
        pid = fav["product_id"]
        product = enriched.get(pid)
        if product is None:
            continue

        skus = product.get("skus", [])
        images = product.get("images", [])

        items.append({
            "id": product["id"],
            "name": product.get("title", product.get("name", "")),
            "slug": product.get("slug"),
            "min_price": skus[0]["price"] if skus else 0,
            "old_price": skus[0].get("old_price") if skus else None,
            "has_stock": any(s.get("active_quantity", 0) > 0 for s in skus),
            "rating": product.get("rating"),
            "reviews_count": product.get("reviews_count", 0),
            "images": [
                {"id": img.get("id", ""), "url": img["url"], "alt": img.get("alt", ""), "ordering": img.get("ordering", 0), "is_main": img.get("is_main", False)}
                for img in images
            ],
            "seller": product.get("seller"),
            "added_at": fav["added_at"],
        })

    return {"items": items, "total_count": total, "limit": limit, "offset": offset}
