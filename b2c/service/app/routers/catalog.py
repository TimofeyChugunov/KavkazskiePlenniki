import re
from fastapi import APIRouter, HTTPException, Query, Request, status
import httpx

from ..config import settings

router = APIRouter(prefix="/api/v1", tags=["Catalog"])

B2B_SERVICE_HEADERS = {"X-Service-Key": settings.B2C_TO_B2B_KEY}

VALID_SORT_VALUES = ["rating", "popularity", "price_asc", "price_desc", "date_desc", "discount_desc"]

SORT_MAPPING = {
    "rating": "popular",
    "popularity": "popular",
    "price_asc": "price_asc",
    "price_desc": "price_desc",
    "date_desc": "created_desc",
    "discount_desc": "created_desc",
}

CHARACTERISTIC_NAME_TO_FILTER = {
    "бренд": "brand",
    "brand": "brand",
    "память": "memory",
    "memory": "memory",
    "цвет": "color",
    "color": "color",
}

FILTER_DISPLAY_NAMES = {
    "brand": "Бренд",
    "memory": "Объём памяти",
    "color": "Цвет",
}


def parse_deep_object_filters(request: Request, prefix: str = "filters") -> dict[str, str | list[str]]:
    result: dict[str, str | list[str]] = {}
    for key, value in request.query_params.items():
        if key.startswith(f"{prefix}[") and key.endswith("]"):
            filter_name = key[len(f"{prefix}["):-1]
            if filter_name in result:
                existing = result[filter_name]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    result[filter_name] = [existing, value]
            else:
                result[filter_name] = value
    return result


def build_b2b_params(
    limit: int,
    offset: int,
    category_id: str | None,
    b2b_sort: str,
    filters: dict[str, str | list[str]],
) -> dict:
    params: dict = {
        "limit": limit,
        "offset": offset,
        "sort": b2b_sort,
    }
    if category_id:
        params["category_id"] = category_id
    for key, value in filters.items():
        if isinstance(value, list):
            params[f"filters[{key}]"] = value
        else:
            params[f"filters[{key}]"] = value
    return params


async def fetch_from_b2b(path: str, params: dict | None = None) -> tuple[int, dict | str]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.B2B_URL}{path}",
            headers=B2B_SERVICE_HEADERS,
            params=params or {},
            timeout=10.0,
        )
    ct = resp.headers.get("content-type", "")
    body = resp.json() if "json" in ct else resp.text
    return resp.status_code, body


def check_b2b_status(status_code: int):
    if status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Category not found"},
        )
    if status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service temporarily unavailable"},
        )
    if status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "B2B_ERROR", "message": f"B2B returned {status_code}"},
        )


@router.get("/products", status_code=status.HTTP_200_OK)
async def list_products(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category_id: str | None = Query(default=None),
    sort: str = Query(default="rating"),
    search: str | None = Query(default=None),
    x_session_id: str | None = Query(default=None, alias="X-Session-Id"),
):
    if sort not in VALID_SORT_VALUES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_REQUEST",
                "message": f"Invalid sort parameter. Allowed: {', '.join(VALID_SORT_VALUES)}",
            },
        )

    if search is not None and len(search) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_REQUEST",
                "message": "Search query must be at least 3 characters",
            },
        )

    if search is not None and len(search) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_REQUEST",
                "message": "Search query must be at most 255 characters",
            },
        )

    b2b_sort = SORT_MAPPING.get(sort, "created_desc")
    filters = parse_deep_object_filters(request, "filters")
    params = build_b2b_params(limit, offset, category_id, b2b_sort, filters)

    if search:
        params["search"] = search

    status_code, data = await fetch_from_b2b("/api/v1/public/products", params)
    check_b2b_status(status_code)

    items = []
    for p in data.get("items", []):
        skus = p.get("skus", [])
        sku = skus[0] if skus else {}
        price = sku.get("price", 0)

        items.append({
            "id": p["id"],
            "title": p["title"],
            "image": p["images"][0]["url"] if p.get("images") else None,
            "price": price,
            "in_stock": any(s.get("active_quantity", 0) > 0 for s in skus),
            "is_in_cart": False,
        })

    return {
        "items": items,
        "total_count": data.get("total_count", 0),
        "limit": limit,
        "offset": offset,
    }


@router.get("/products/{product_id}", status_code=status.HTTP_200_OK)
async def get_product_card(
    product_id: str,
    sku: str | None = Query(default=None),
):
    if not re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", product_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )

    status_code, data = await fetch_from_b2b(f"/api/v1/public/products/{product_id}")
    check_b2b_status(status_code)

    return data


@router.get("/products/{product_id}/similar", status_code=status.HTTP_200_OK)
async def get_similar_products(
    product_id: str,
    category: str = Query(),
    limit: int = Query(default=8, ge=1, le=20),
    offset: int = Query(default=0, ge=0),
):
    if not re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", product_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )

    params: dict = {
        "category": category,
        "limit": limit,
        "offset": offset,
    }

    status_code, data = await fetch_from_b2b(
        f"/api/v1/public/products/{product_id}/similar",
        params,
    )

    if status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )
    if status_code == 400:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": data.get("message", "Nonexistent category id") if isinstance(data, dict) else "Nonexistent category id"},
        )
    check_b2b_status(status_code)

    items = data.get("items", [])
    mapped_items = []
    for item in items:
        mapped_items.append({
            "id": item["id"],
            "title": item["title"],
            "image": item.get("image"),
            "price": item.get("price", 0),
            "in_stock": item.get("in_stock", False),
            "is_in_cart": item.get("is_in_cart", False),
        })

    return {
        "items": mapped_items,
        "total_count": data.get("total_count", 0),
        "limit": data.get("limit", limit),
        "offset": data.get("offset", offset),
    }


@router.get("/categories/{category_id}/filters", status_code=status.HTTP_200_OK)
async def get_category_filters(category_id: str):
    status_code, data = await fetch_from_b2b(
        "/api/v1/public/products",
        {"category_id": category_id, "limit": 1000},
    )
    check_b2b_status(status_code)

    products = data.get("items", [])
    char_values: dict[str, set[str]] = {}
    price_min: int | None = None
    price_max: int | None = None

    for p in products:
        for char in p.get("characteristics", []):
            slug = CHARACTERISTIC_NAME_TO_FILTER.get(char["name"].lower())
            if slug:
                char_values.setdefault(slug, set()).add(char["value"])
        for sku in p.get("skus", []):
            price = sku.get("price", 0)
            if price_min is None or price < price_min:
                price_min = price
            if price_max is None or price > price_max:
                price_max = price

    filters_list = []
    for slug, values in sorted(char_values.items()):
        filters_list.append({
            "slug": slug,
            "name": FILTER_DISPLAY_NAMES.get(slug, slug),
            "type": "list",
            "value": sorted(values),
        })
    if price_min is not None and price_max is not None:
        filters_list.append({
            "slug": "price",
            "name": "Цена",
            "type": "range",
            "min": price_min,
            "max": price_max,
        })

    return {"items": filters_list}


@router.get("/catalog/facets", status_code=status.HTTP_200_OK)
async def get_catalog_facets(
    request: Request,
    category_id: str | None = Query(default=None),
):
    filters = parse_deep_object_filters(request, "filters")
    params: dict = {"limit": 1000}
    if category_id:
        params["category_id"] = category_id
    for key, value in filters.items():
        if isinstance(value, list):
            params[f"filters[{key}]"] = value
        else:
            params[f"filters[{key}]"] = value

    status_code, data = await fetch_from_b2b("/api/v1/public/products", params)
    check_b2b_status(status_code)

    products = data.get("items", [])
    facet_map: dict[str, dict[str, int]] = {}

    for p in products:
        for char in p.get("characteristics", []):
            name = char["name"]
            value = char["value"]
            if name not in facet_map:
                facet_map[name] = {}
            facet_map[name][value] = facet_map[name].get(value, 0) + 1

        for sku in p.get("skus", []):
            if "price" not in facet_map:
                facet_map["price"] = {}
            price = sku.get("price", 0)
            price_key = str(price)
            facet_map["price"][price_key] = facet_map["price"].get(price_key, 0) + 1

    facets = []
    for name, values_dict in facet_map.items():
        facet_values = [
            {"value": v, "count": c}
            for v, c in sorted(values_dict.items(), key=lambda x: -x[1])
        ]
        facets.append({"name": name, "values": facet_values})

    return {
        "category_id": category_id,
        "facets": facets,
    }
