import re

from fastapi import APIRouter, HTTPException, Query, status

from ..config import settings

router = APIRouter(prefix="/api/v1", tags=["Categories"])

CATEGORIES_CACHE: list[dict] | None = None


async def fetch_flat_categories_from_b2b() -> list[dict]:
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.B2B_URL}/api/v1/public/categories",
            headers={"X-Service-Key": settings.B2C_TO_B2B_KEY},
            timeout=10.0,
        )
    if resp.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Category not found"},
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


def build_tree(flat: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    for cat in flat:
        by_id[cat["id"]] = {**cat, "children": []}

    roots: list[dict] = []
    for cat in flat:
        node = by_id[cat["id"]]
        pid = cat.get("parent_id")
        if pid and pid in by_id:
            by_id[pid]["children"].append(node)
        elif pid and pid not in by_id:
            raise ValueError(f"orphan_node: parent {pid} not found for category {cat['id']}")
        else:
            roots.append(node)
    return roots


def flatten_tree(tree: list[dict]) -> list[dict]:
    result: list[dict] = []
    for node in tree:
        result.append({
            "id": node["id"],
            "name": node["name"],
            "slug": node.get("slug", node["name"].lower().replace(" ", "-")),
            "parent_id": node.get("parent_id"),
            "children": node.get("children", []),
        })
        result.extend(flatten_tree(node.get("children", [])))
    return result


def build_breadcrumbs_from_tree(tree: list[dict], target_id: str) -> list[dict] | None:
    path: list[dict] = []

    def dfs(nodes: list[dict], depth: int) -> bool:
        for node in nodes:
            path.append({
                "id": node["id"],
                "slug": node.get("slug", node["name"].lower().replace(" ", "-")),
                "name": node["name"],
                "level": depth,
                "is_current": node["id"] == target_id,
            })
            if node["id"] == target_id:
                return True
            if dfs(node.get("children", []), depth + 1):
                return True
            path.pop()
        return False

    if dfs(tree, 0):
        return path
    return None


def build_url_from_breadcrumbs(breadcrumbs: list[dict]) -> list[dict]:
    result: list[dict] = []
    for i, crumb in enumerate(breadcrumbs):
        url_parts = [b.get("slug", b["name"].lower().replace(" ", "-")) for b in breadcrumbs[: i + 1]]
        result.append({**crumb, "url": "/catalog/" + "/".join(url_parts)})
    return result


async def get_categories_tree() -> list[dict]:
    global CATEGORIES_CACHE
    if CATEGORIES_CACHE is not None:
        return CATEGORIES_CACHE

    try:
        flat = await fetch_flat_categories_from_b2b()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service temporarily unavailable"},
        )

    try:
        tree = build_tree(flat)
    except ValueError as e:
        if "orphan_node" in str(e):
            raise HTTPException(
                status_code=422,
                detail={"error": "orphan_node", "message": "category hierarchy is broken"},
            )
        raise

    CATEGORIES_CACHE = tree
    return tree


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@router.get("/categories", status_code=status.HTTP_200_OK)
async def get_category_tree():
    tree = await get_categories_tree()
    flat = flatten_tree(tree)
    return {"items": flat}


@router.get("/categories/{category_id}", status_code=status.HTTP_200_OK)
async def get_category_details(category_id: str, include_product_count: bool = Query(default=False)):
    if not UUID_RE.match(category_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Category not found"},
        )

    tree = await get_categories_tree()
    flat = flatten_tree(tree)
    category = next((c for c in flat if c["id"] == category_id), None)

    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Category not found"},
        )

    parent = None
    if category.get("parent_id"):
        parent_node = next((c for c in flat if c["id"] == category["parent_id"]), None)
        if parent_node:
            parent = {
                "id": parent_node["id"],
                "name": parent_node["name"],
                "slug": parent_node.get("slug", parent_node["name"].lower().replace(" ", "-")),
            }

    product_count = 0
    if include_product_count:
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{settings.B2B_URL}/api/v1/public/products",
                    headers={"X-Service-Key": settings.B2C_TO_B2B_KEY},
                    params={"category_id": category_id, "limit": 1},
                    timeout=10.0,
                )
            if resp.status_code == 200:
                product_count = resp.json().get("total_count", 0)
        except Exception:
            pass

    slug = category.get("slug", category["name"].lower().replace(" ", "-"))
    return {
        "id": category["id"],
        "name": category["name"],
        "slug": slug,
        "description": category.get("description", ""),
        "parent": parent,
        "product_count": product_count,
        "seo": {
            "title": f"Купить {category['name'].lower()} в интернет-магазине | NeoMarket",
            "description": f"{category['name']} по выгодным ценам. Бесплатная доставка.",
            "keywords": [category["name"].lower()],
        },
        "meta_tags": {
            "og_title": f"{category['name']} | NeoMarket",
            "og_description": f"Купить {category['name'].lower()} в интернет-магазине.",
        },
        "image_url": category.get("image_url"),
        "is_active": category.get("is_active", True),
        "created_at": category.get("created_at", "2024-01-15T10:30:00Z"),
        "updated_at": category.get("updated_at", "2024-03-01T14:20:00Z"),
    }


@router.get("/breadcrumbs", status_code=status.HTTP_200_OK)
async def get_breadcrumbs(
    category_id: str | None = Query(default=None),
    product_id: str | None = Query(default=None),
):
    if category_id and product_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "ambiguous_param", "message": "only one of category_id or product_id must be provided"},
        )

    if not category_id and not product_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "missing_param", "message": "category_id or product_id must be provided"},
        )

    import httpx

    resolved_category_id = category_id

    if product_id:
        if not UUID_RE.match(product_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "Product not found"},
            )
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{settings.B2B_URL}/api/v1/public/products/{product_id}",
                    headers={"X-Service-Key": settings.B2C_TO_B2B_KEY},
                    timeout=10.0,
                )
            if resp.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "NOT_FOUND", "message": "Product not found"},
                )
            if resp.status_code >= 500:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={"code": "B2B_UNAVAILABLE", "message": "B2B service temporarily unavailable"},
                )
            product_data = resp.json()
            resolved_category_id = product_data.get("category", {}).get("id")
            if not resolved_category_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "NOT_FOUND", "message": "Product category not found"},
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "B2B_UNAVAILABLE", "message": "B2B service temporarily unavailable"},
            )

    tree = await get_categories_tree()
    crumbs = build_breadcrumbs_from_tree(tree, resolved_category_id)

    if crumbs is None:
        orphan_check = await check_orphan_node(resolved_category_id)
        if orphan_check:
            raise HTTPException(
                status_code=422,
                detail={"error": "orphan_node", "message": "category hierarchy is broken"},
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Category not found"},
        )

    crumbs_with_url = build_url_from_breadcrumbs(crumbs)

    resolved_via = "category_id" if category_id else "product_id"
    meta: dict = {"resolved_via": resolved_via}
    if category_id:
        meta["category_id"] = category_id
    if product_id:
        meta["product_id"] = product_id
        meta["category_id"] = resolved_category_id

    return {"data": crumbs_with_url, "meta": meta}


async def check_orphan_node(category_id: str) -> bool:
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.B2B_URL}/api/v1/public/categories/{category_id}",
                headers={"X-Service-Key": settings.B2C_TO_B2B_KEY},
                timeout=10.0,
            )
        if resp.status_code == 200:
            cat = resp.json()
            parent_id = cat.get("parent_id")
            if parent_id:
                flat = await fetch_flat_categories_from_b2b()
                if not any(c["id"] == parent_id for c in flat):
                    return True
    except Exception:
        pass
    return False
