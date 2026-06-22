import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("categories.id"), nullable=True)
    level: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str] = mapped_column(String(500), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    seller_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    category_id: Mapped[str] = mapped_column(String(36), ForeignKey("categories.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CREATED")
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    images: Mapped[list["ProductImage"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    characteristics: Mapped[list["Characteristic"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    skus: Mapped[list["SKU"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    blocking_reason: Mapped["BlockingReason | None"] = relationship(back_populates="product", uselist=False, cascade="all, delete-orphan")
    field_reports: Mapped[list["FieldReport"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    ordering: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped["Product"] = relationship(back_populates="images")


class Characteristic(Base):
    __tablename__ = "characteristics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=True)
    sku_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("skus.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(String(255), nullable=False)

    product: Mapped["Product | None"] = relationship(back_populates="characteristics")
    sku: Mapped["SKU | None"] = relationship(back_populates="characteristics")


class SKU(Base):
    __tablename__ = "skus"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    article: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    product: Mapped["Product"] = relationship(back_populates="skus")
    characteristics: Mapped[list["Characteristic"]] = relationship(back_populates="sku")


class BlockingReason(Base):
    __tablename__ = "blocking_reasons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")

    product: Mapped["Product"] = relationship(back_populates="blocking_reason")


class FieldReport(Base):
    __tablename__ = "field_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    sku_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")

    product: Mapped["Product"] = relationship(back_populates="field_reports")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    seller_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    items: Mapped[list["InvoiceItem"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    invoice_id: Mapped[str] = mapped_column(String(36), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    sku_id: Mapped[str] = mapped_column(String(36), ForeignKey("skus.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    invoice: Mapped["Invoice"] = relationship(back_populates="items")
    sku: Mapped["SKU"] = relationship()


class ReserveOperation(Base):
    __tablename__ = "reserve_operations"

    idempotency_key: Mapped[str] = mapped_column(String(36), primary_key=True)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
