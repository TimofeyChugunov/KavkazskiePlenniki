import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..models import Category, Characteristic, Product, ProductImage, SKU
from .skus import send_moderation_event
from ..schemas import ErrorResponse, ProductCreate, ProductResponse, ProductUpdate

router = APIRouter(prefix="/api/v1/products", tags=["Products"])


def slugify(title: str) -> str:
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug[:255]


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
