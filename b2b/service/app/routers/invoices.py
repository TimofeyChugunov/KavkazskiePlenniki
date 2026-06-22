from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..models import Invoice, InvoiceItem, Product, SKU
from ..schemas import ErrorResponse, InvoiceCreate, InvoiceResponse

router = APIRouter(prefix="/api/v1/invoices", tags=["Invoices"])


@router.post(
    "",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def create_invoice(
    body: InvoiceCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    seller_id = current_user.get("sub")
    if not seller_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid token"},
        )

    sku_ids = [item.sku_id for item in body.items]
    result = await db.execute(select(SKU).where(SKU.id.in_(sku_ids)))
    skus = {sku.id: sku for sku in result.scalars().all()}

    for item in body.items:
        if item.sku_id not in skus:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "SKU not found"},
            )

    for item in body.items:
        sku = skus[item.sku_id]
        product = await db.get(Product, sku.product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "SKU not found"},
            )
        if product.seller_id != seller_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "NOT_OWNER", "message": "One or more SKUs do not belong to the authenticated seller"},
            )
        if product.status != "MODERATED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_REQUEST", "message": "Invoice can only be created for MODERATED products"},
            )

    invoice = Invoice(
        seller_id=seller_id,
        status="PENDING",
    )
    db.add(invoice)
    await db.flush()

    for item in body.items:
        db.add(InvoiceItem(
            invoice_id=invoice.id,
            sku_id=item.sku_id,
            quantity=item.quantity,
        ))

    await db.commit()
    await db.refresh(invoice)

    items_data = []
    for ii in (await db.execute(
        select(InvoiceItem).where(InvoiceItem.invoice_id == invoice.id)
    )).scalars().all():
        sku = skus[ii.sku_id]
        items_data.append({
            "id": ii.id,
            "sku_id": ii.sku_id,
            "sku_name": sku.name,
            "quantity": ii.quantity,
            "accepted_quantity": ii.accepted_quantity,
        })

    return {
        "id": invoice.id,
        "seller_id": invoice.seller_id,
        "status": invoice.status,
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
        "items": items_data,
    }
