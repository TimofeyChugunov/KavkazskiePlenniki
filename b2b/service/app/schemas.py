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
