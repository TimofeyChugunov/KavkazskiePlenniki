from datetime import datetime
from pydantic import BaseModel, Field


class ImageCreate(BaseModel):
    url: str = Field(..., min_length=1, max_length=500)
    ordering: int = Field(..., ge=0)


class CharacteristicCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    value: str = Field(..., min_length=1, max_length=255)


class ProductCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=5000)
    category_id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    images: list[ImageCreate] = Field(..., min_length=1)
    characteristics: list[CharacteristicCreate] = Field(default_factory=list)


class ImageResponse(BaseModel):
    url: str
    ordering: int

    model_config = {"from_attributes": True}


class CharacteristicResponse(BaseModel):
    name: str
    value: str

    model_config = {"from_attributes": True}


class CategoryRef(BaseModel):
    id: str
    name: str

    model_config = {"from_attributes": True}


class SKUResponse(BaseModel):
    id: str
    product_id: str
    name: str
    price: int
    discount: int
    cost_price: int | None = None
    stock_quantity: int
    active_quantity: int
    reserved_quantity: int
    article: str | None = None
    images: list = []
    characteristics: list = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProductResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str
    deleted: bool
    blocked: bool
    category: CategoryRef
    images: list[ImageResponse]
    characteristics: list[CharacteristicResponse]
    skus: list[SKUResponse]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict | None = None


class SKUCreate(BaseModel):
    product_id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    name: str = Field(..., min_length=1, max_length=255)
    price: int = Field(..., gt=0)
    cost_price: int = Field(..., gt=0)
    discount: int = Field(default=0, ge=0)
    image: str = Field(..., min_length=1, max_length=500)
    characteristics: list[CharacteristicCreate] = Field(default_factory=list)


class SKUCreateResponse(BaseModel):
    id: str
    product_id: str
    name: str
    price: int
    cost_price: int
    discount: int
    image: str
    active_quantity: int
    reserved_quantity: int
    characteristics: list[CharacteristicResponse]

    model_config = {"from_attributes": True}


class ProductUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    category_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    images: list[ImageCreate] | None = Field(default=None, min_length=1)
    characteristics: list[CharacteristicCreate] | None = None


class SKUUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    price: int | None = Field(default=None, gt=0)
    cost_price: int | None = Field(default=None, gt=0)
    discount: int | None = Field(default=None, ge=0)
    image: str | None = Field(default=None, min_length=1, max_length=500)
    characteristics: list[CharacteristicCreate] | None = None


class BlockingReasonResponse(BaseModel):
    id: str
    title: str
    comment: str

    model_config = {"from_attributes": True}


class FieldReportResponse(BaseModel):
    field_name: str
    sku_id: str | None = None
    comment: str

    model_config = {"from_attributes": True}


class SKUDetailResponse(BaseModel):
    id: str
    name: str
    price: int
    cost_price: int | None = None
    discount: int
    image: str | None = None
    active_quantity: int
    reserved_quantity: int
    characteristics: list[CharacteristicResponse] = []

    model_config = {"from_attributes": True}


class ProductDetailResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str
    deleted: bool
    blocked: bool
    category: CategoryRef
    images: list[ImageResponse]
    characteristics: list[CharacteristicResponse]
    skus: list[SKUDetailResponse]
    blocking_reason: BlockingReasonResponse | None = None
    field_reports: list[FieldReportResponse] = []

    model_config = {"from_attributes": True}


class InvoiceItemCreate(BaseModel):
    sku_id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    quantity: int = Field(..., gt=0)


class InvoiceCreate(BaseModel):
    items: list[InvoiceItemCreate] = Field(..., min_length=1)


class InvoiceItemResponse(BaseModel):
    id: str
    sku_id: str
    sku_name: str
    quantity: int
    accepted_quantity: int | None = None

    model_config = {"from_attributes": True}


class InvoiceResponse(BaseModel):
    id: str
    seller_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    items: list[InvoiceItemResponse]

    model_config = {"from_attributes": True}


class InventoryItem(BaseModel):
    sku_id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    quantity: int = Field(..., gt=0)


class ReserveRequest(BaseModel):
    idempotency_key: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    items: list[InventoryItem] = Field(..., min_length=1)


class ReserveSuccessItem(BaseModel):
    sku_id: str
    reserved_quantity: int
    remaining_stock: int


class ReserveSuccessResponse(BaseModel):
    reserved: bool = True
    items: list[ReserveSuccessItem]


class ReserveFailedItem(BaseModel):
    sku_id: str
    requested: int
    available: int
    reason: str


class ReserveFailResponse(BaseModel):
    reserved: bool = False
    failed_items: list[ReserveFailedItem]


class UnreserveItem(BaseModel):
    sku_id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    quantity: int = Field(..., gt=0)


class UnreserveRequest(BaseModel):
    order_id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    items: list[UnreserveItem] = Field(..., min_length=1)


class UnreserveResponse(BaseModel):
    ok: bool = True


class ModerationBlockingReason(BaseModel):
    id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    title: str
    comment: str


class ModerationFieldReport(BaseModel):
    field_name: str
    sku_id: str | None = None
    comment: str


class ModerationEventRequest(BaseModel):
    idempotency_key: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    product_id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    status: str = Field(..., pattern=r"^(MODERATED|BLOCKED)$")
    hard_block: bool | None = None
    blocking_reason: ModerationBlockingReason | None = None
    field_reports: list[ModerationFieldReport] | None = None


class FulfillItem(BaseModel):
    sku_id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    quantity: int = Field(..., gt=0)


class FulfillRequest(BaseModel):
    order_id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    items: list[FulfillItem] = Field(..., min_length=1)


class FulfillResponse(BaseModel):
    ok: bool = True
