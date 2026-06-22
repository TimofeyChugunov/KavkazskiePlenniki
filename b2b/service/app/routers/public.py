from fastapi import APIRouter, Depends, HTTPException, Header, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..database import get_db
from ..models import Category, Characteristic, Product, ProductImage, SKU

router = APIRouter(prefix="/api/v1/public", tags=["Public Catalog"])


async def verify_service_key(x_service_key: str | None = Header(default=None)):
    if not x_service_key or x_service_key != settings.B2C_TO_B2B_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing X-Service-Key"},
        )
    return x_service_key


def build_catalog_query(
    category_id: str | None = None,
    search: str | None = None,
    sort: str = "created_desc",
    product_ids: list[str] | None = None,
):
    subq = (
        select(SKU.product_id)
        .where(SKU.active_quantity > 0)
        .group_by(SKU.product_id)
        .having(func.sum(SKU.active_quantity) > 0)
    )

    query = (
        select(Product)
        .where(Product.status == "MODERATED")
        .where(Product.deleted == False)
        .where(Product.id.in_(subq))
    )

    if product_ids:
        query = query.where(Product.id.in_(product_ids))

    if category_id:
        query = query.where(Product.category_id == category_id)

    if search and len(search) >= 3:
        search_pattern = f"%{search}%"
        query = query.where(
            Product.title.ilike(search_pattern) | Product.description.ilike(search_pattern)
        )

    if sort == "price_asc":
        min_price_subq = (
            select(SKU.product_id, func.min(SKU.price).label("min_price"))
            .group_by(SKU.product_id)
            .subquery()
        )
        query = query.join(min_price_subq, Product.id == min_price_subq.c.product_id)
        query = query.order_by(min_price_subq.c.min_price.asc())
    elif sort == "price_desc":
        min_price_subq = (
            select(SKU.product_id, func.min(SKU.price).label("min_price"))
            .group_by(SKU.product_id)
            .subquery()
        )
        query = query.join(min_price_subq, Product.id == min_price_subq.c.product_id)
        query = query.order_by(min_price_subq.c.min_price.desc())
    else:
        query = query.order_by(Product.created_at.desc())

    return query


async def build_product_response(product: Product, db: AsyncSession) -> dict:
    category = await db.get(Category, product.category_id)
    images = (
        await db.execute(
            select(ProductImage).where(ProductImage.product_id == product.id)
        )
    ).scalars().all()
    chars = (
        await db.execute(
            select(Characteristic).where(
                Characteristic.product_id == product.id, Characteristic.sku_id.is_(None)
            )
        )
    ).scalars().all()
    skus = (
        await db.execute(select(SKU).where(SKU.product_id == product.id))
    ).scalars().all()

    sku_chars = {}
    sku_ids = [sku.id for sku in skus]
    if sku_ids:
        all_sku_chars = (
            await db.execute(
                select(Characteristic).where(Characteristic.sku_id.in_(sku_ids))
            )
        ).scalars().all()
        for char in all_sku_chars:
            if char.sku_id:
                sku_chars.setdefault(char.sku_id, []).append(
                    {"name": char.name, "value": char.value}
                )

    return {
        "id": product.id,
        "title": product.title,
        "description": product.description,
        "status": product.status,
        "category": {"id": category.id, "name": category.name} if category else None,
        "images": [
            {"url": i.url, "ordering": i.ordering}
            for i in sorted(images, key=lambda x: x.ordering)
        ],
        "characteristics": [{"name": c.name, "value": c.value} for c in chars],
        "skus": [
            {
                "id": sku.id,
                "name": sku.name,
                "price": sku.price,
                "discount": sku.discount,
                "image": sku.image,
                "active_quantity": sku.active_quantity,
                "characteristics": sku_chars.get(sku.id, []),
            }
            for sku in skus
        ],
    }


@router.get(
    "/products",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "OK"},
        401: {"model": dict},
    },
)
async def list_public_products(
    _key: str = Depends(verify_service_key),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category_id: str | None = Query(default=None),
    search: str | None = Query(default=None, min_length=3),
    sort: str = Query(default="created_desc", pattern="^(price_asc|price_desc|created_desc)$"),
    ids: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    product_ids = [i.strip() for i in ids.split(",") if i.strip()] if ids else None

    query = build_catalog_query(
        category_id=category_id,
        search=search,
        sort=sort,
        product_ids=product_ids,
    )

    count_query = build_catalog_query(
        category_id=category_id,
        search=search,
        sort=sort,
        product_ids=product_ids,
    )

    total = (await db.execute(
        select(func.count()).select_from(count_query.subquery())
    )).scalar() or 0

    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    products = result.scalars().all()

    items = []
    for p in products:
        items.append(await build_product_response(p, db))

    return {
        "items": items,
        "total_count": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/products/{product_id}",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "OK"},
        401: {"model": dict},
        404: {"model": dict},
    },
)
async def get_public_product(
    product_id: str,
    _key: str = Depends(verify_service_key),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )

    if product.status != "MODERATED" or product.deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )

    return await build_product_response(product, db)
