import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..models import Category, Characteristic, Product, ProductImage
from ..schemas import ErrorResponse, ProductCreate, ProductResponse

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
