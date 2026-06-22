import re
import uuid
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, get_optional_user
from ..config import settings
from ..database import get_db
from ..models import BlockingReason, Category, Characteristic, FieldReport, Product, ProductImage, SKU
from .skus import send_moderation_event
from ..schemas import ErrorResponse, ProductCreate, ProductDetailResponse, ProductResponse, ProductUpdate

router = APIRouter(prefix="/api/v1/products", tags=["Products"])


def slugify(title: str) -> str:
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug[:255]


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "OK"},
        401: {"model": ErrorResponse},
    },
)
async def list_my_products(
    limit: int = 20,
    offset: int = 0,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    include_deleted: bool = False,
    search: str | None = None,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    seller_id = current_user.get("sub")
    if not seller_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid token"},
        )

    query = select(Product).where(Product.seller_id == seller_id)

    if not include_deleted:
        query = query.where(Product.deleted == False)

    if status_filter:
        query = query.where(Product.status == status_filter)

    if search:
        query = query.where(Product.title.ilike(f"%{search}%"))

    count_query = select(func.count()).select_from(Product).where(Product.seller_id == seller_id)
    if not include_deleted:
        count_query = count_query.where(Product.deleted == False)
    if status_filter:
        count_query = count_query.where(Product.status == status_filter)
    if search:
        count_query = count_query.where(Product.title.ilike(f"%{search}%"))

    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Product.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    products = result.scalars().all()

    product_ids = [p.id for p in products]

    skus_count_map = {}
    total_active_map = {}
    if product_ids:
        skus_count_rows = (await db.execute(
            select(SKU.product_id, func.count(SKU.id))
            .where(SKU.product_id.in_(product_ids))
            .group_by(SKU.product_id)
        )).all()
        for row in skus_count_rows:
            skus_count_map[row[0]] = row[1]

        active_rows = (await db.execute(
            select(SKU.product_id, func.coalesce(func.sum(SKU.active_quantity), 0))
            .where(SKU.product_id.in_(product_ids))
            .group_by(SKU.product_id)
        )).all()
        for row in active_rows:
            total_active_map[row[0]] = row[1]

    categories = {}
    if product_ids:
        category_ids = list({p.category_id for p in products})
        cat_rows = (await db.execute(
            select(Category).where(Category.id.in_(category_ids))
        )).scalars().all()
        categories = {c.id: c for c in cat_rows}

    images_map = {}
    if product_ids:
        img_rows = (await db.execute(
            select(ProductImage).where(ProductImage.product_id.in_(product_ids))
        )).scalars().all()
        for img in img_rows:
            if img.product_id not in images_map:
                images_map[img.product_id] = []
            images_map[img.product_id].append(img)

    items = []
    for p in products:
        cat = categories.get(p.category_id)
        product_images = sorted(images_map.get(p.id, []), key=lambda x: x.ordering)
        items.append({
            "id": p.id,
            "title": p.title,
            "status": p.status,
            "category": {"id": cat.id, "name": cat.name} if cat else None,
            "images": [{"url": i.url, "ordering": i.ordering} for i in product_images],
            "skus_count": skus_count_map.get(p.id, 0),
            "total_active_quantity": total_active_map.get(p.id, 0),
            "created_at": p.created_at,
        })

    return {
        "items": items,
        "total_count": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{product_id}",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "OK"},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_product(
    product_id: str,
    request: Request,
    x_service_key: str | None = Header(default=None),
    current_user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    is_service_call = x_service_key and x_service_key == settings.B2B_TO_MOD_KEY

    if not is_service_call and not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid token"},
        )

    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )

    if not is_service_call:
        seller_id = current_user.get("sub")
        if not seller_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "UNAUTHORIZED", "message": "Invalid token"},
            )
        if product.seller_id != seller_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "Product not found"},
            )

    category = await db.get(Category, product.category_id)
    images = (await db.execute(select(ProductImage).where(ProductImage.product_id == product.id))).scalars().all()
    chars = (await db.execute(select(Characteristic).where(Characteristic.product_id == product.id))).scalars().all()
    skus = (await db.execute(select(SKU).where(SKU.product_id == product.id))).scalars().all()

    sku_chars = {}
    sku_ids = [sku.id for sku in skus]
    if sku_ids:
        all_sku_chars = (await db.execute(select(Characteristic).where(Characteristic.sku_id.in_(sku_ids)))).scalars().all()
        for char in all_sku_chars:
            if char.sku_id:
                if char.sku_id not in sku_chars:
                    sku_chars[char.sku_id] = []
                sku_chars[char.sku_id].append({"name": char.name, "value": char.value})

    skus_data = []
    for sku in skus:
        skus_data.append({
            "id": sku.id,
            "name": sku.name,
            "price": sku.price,
            "cost_price": sku.cost_price if not is_service_call else None,
            "discount": sku.discount,
            "image": sku.image,
            "active_quantity": sku.active_quantity,
            "reserved_quantity": sku.reserved_quantity if not is_service_call else None,
            "characteristics": sku_chars.get(sku.id, []),
        })

    blocking_reason_data = None
    blocking_reason = (await db.execute(select(BlockingReason).where(BlockingReason.product_id == product.id))).scalars().first()
    if blocking_reason:
        blocking_reason_data = {
            "id": blocking_reason.id,
            "title": blocking_reason.title,
            "comment": blocking_reason.comment,
        }

    field_reports_data = []
    for fr in (await db.execute(select(FieldReport).where(FieldReport.product_id == product.id))).scalars().all():
        field_reports_data.append({
            "field_name": fr.field_name,
            "sku_id": fr.sku_id,
            "comment": fr.comment,
        })

    return {
        "id": product.id,
        "title": product.title,
        "description": product.description,
        "status": product.status,
        "deleted": product.deleted,
        "blocked": product.blocked,
        "category": {"id": category.id, "name": category.name},
        "images": [{"url": i.url, "ordering": i.ordering} for i in sorted(images, key=lambda x: x.ordering)],
        "characteristics": [{"name": c.name, "value": c.value} for c in chars if not c.sku_id],
        "skus": skus_data,
        "blocking_reason": blocking_reason_data,
        "field_reports": field_reports_data,
    }


async def send_b2c_product_deleted(product_id: str, sku_ids: list[str]) -> None:
    idempotency_key = str(uuid.uuid4())
    payload = {
        "idempotency_key": idempotency_key,
        "event": "PRODUCT_DELETED",
        "product_id": product_id,
        "sku_ids": sku_ids,
        "date": datetime.now(timezone.utc).isoformat(),
    }
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{settings.B2C_URL}/api/v1/events/product",
            json=payload,
            headers={"X-Service-Key": settings.B2B_TO_B2C_KEY},
            timeout=5.0,
        )


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
    },
)
async def create_product(
    body: ProductCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    seller_id = current_user.get("sub")
    if not seller_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid token"},
        )

    category = await db.get(Category, body.category_id)
    if not category:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": "Category not found"},
        )

    product = Product(
        seller_id=seller_id,
        category_id=body.category_id,
        title=body.title,
        slug=slugify(body.title),
        description=body.description,
        status="CREATED",
        deleted=False,
        blocked=False,
    )
    db.add(product)
    await db.flush()

    for img in body.images:
        db.add(ProductImage(product_id=product.id, url=img.url, ordering=img.ordering))

    for char in body.characteristics:
        db.add(Characteristic(product_id=product.id, name=char.name, value=char.value))

    await db.commit()
    await db.refresh(product)

    images = (await db.execute(select(ProductImage).where(ProductImage.product_id == product.id))).scalars().all()
    chars = (await db.execute(select(Characteristic).where(Characteristic.product_id == product.id))).scalars().all()

    return ProductResponse(
        id=product.id,
        title=product.title,
        description=product.description,
        status=product.status,
        deleted=product.deleted,
        blocked=product.blocked,
        category={"id": category.id, "name": category.name},
        images=[{"url": i.url, "ordering": i.ordering} for i in sorted(images, key=lambda x: x.ordering)],
        characteristics=[{"name": c.name, "value": c.value} for c in chars],
        skus=[],
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Помечен удалённым"},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def delete_product(
    product_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    seller_id = current_user.get("sub")
    if not seller_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid token"},
        )

    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )

    if product.seller_id != seller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NOT_OWNER", "message": "Product does not belong to the authenticated seller"},
        )

    if product.status == "HARD_BLOCKED":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Cannot delete hard-blocked product"},
        )

    if product.deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": "Product already deleted"},
        )

    product.deleted = True
    await db.commit()

    sku_ids = [
        sku.id
        for sku in (await db.execute(select(SKU).where(SKU.product_id == product.id))).scalars().all()
    ]

    await send_moderation_event(
        product_id=product.id,
        seller_id=seller_id,
        event="DELETED",
    )

    await send_b2c_product_deleted(
        product_id=product.id,
        sku_ids=sku_ids,
    )

    return {"ok": True}

@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def update_product(
    product_id: str,
    body: ProductUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    seller_id = current_user.get("sub")
    if not seller_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid token"},
        )

    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )

    if product.seller_id != seller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NOT_OWNER", "message": "Product does not belong to the authenticated seller"},
        )

    if product.status == "HARD_BLOCKED":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Cannot edit hard-blocked product"},
        )

    if body.title is not None:
        product.title = body.title
        product.slug = slugify(body.title)
    if body.description is not None:
        product.description = body.description
    if body.category_id is not None:
        category = await db.get(Category, body.category_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_REQUEST", "message": "Category not found"},
            )
        product.category_id = body.category_id

    event_to_send = None
    if product.status in ("MODERATED", "BLOCKED"):
        product.status = "ON_MODERATION"
        event_to_send = "EDITED"

    if body.images is not None:
        old_images = (await db.execute(select(ProductImage).where(ProductImage.product_id == product.id))).scalars().all()
        for img in old_images:
            await db.delete(img)
        for img in body.images:
            db.add(ProductImage(product_id=product.id, url=img.url, ordering=img.ordering))

    if body.characteristics is not None:
        old_chars = (await db.execute(select(Characteristic).where(Characteristic.product_id == product.id))).scalars().all()
        for char in old_chars:
            await db.delete(char)
        for char in body.characteristics:
            db.add(Characteristic(product_id=product.id, name=char.name, value=char.value))

    await db.commit()
    await db.refresh(product)

    if event_to_send:
        await send_moderation_event(
            product_id=product.id,
            seller_id=seller_id,
            event=event_to_send,
        )

    category = await db.get(Category, product.category_id)
    images = (await db.execute(select(ProductImage).where(ProductImage.product_id == product.id))).scalars().all()
    chars = (await db.execute(select(Characteristic).where(Characteristic.product_id == product.id))).scalars().all()
    skus = (await db.execute(select(SKU).where(SKU.product_id == product.id))).scalars().all()

    return ProductResponse(
        id=product.id,
        title=product.title,
        description=product.description,
        status=product.status,
        deleted=product.deleted,
        blocked=product.blocked,
        category={"id": category.id, "name": category.name},
        images=[{"url": i.url, "ordering": i.ordering} for i in sorted(images, key=lambda x: x.ordering)],
        characteristics=[{"name": c.name, "value": c.value} for c in chars],
        skus=[],
        created_at=product.created_at,
        updated_at=product.updated_at,
    )
