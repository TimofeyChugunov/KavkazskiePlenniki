from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status

from .database import init_db
from .routers import fulfill, invoices, inventory, moderation, products, public, skus


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = first.get("loc", ())
        field = loc[-1] if loc else "body"
        msg_type = first.get("type", "")

        field_messages = {
            "title": {
                "missing": "title is required",
                "string_too_short": "title must be 1-255 characters",
                "string_too_long": "title must be 1-255 characters",
                "value_error": "title is required",
            },
            "description": {
                "missing": "description is required",
                "string_too_short": "description must be 1-5000 characters",
                "string_too_long": "description must be 1-5000 characters",
            },
            "category_id": {
                "missing": "category_id is required",
                "value_error": "category_id must be a valid UUID",
                "string_pattern_mismatch": "category_id must be a valid UUID",
            },
            "images": {
                "missing": "images is required",
                "too_short": "At least one image is required",
                "value_error": "At least one image is required",
            },
            "ordering": {
                "missing": "ordering is required",
            },
            "url": {
                "missing": "url is required",
            },
            "image": {
                "missing": "image is required",
            },
            "product_id": {
                "missing": "product_id is required",
                "value_error": "product_id must be a valid UUID",
                "string_pattern_mismatch": "product_id must be a valid UUID",
            },
            "price": {
                "missing": "price is required",
                "value_error": "price must be a positive integer (kopecks)",
            },
            "cost_price": {
                "missing": "cost_price is required",
                "value_error": "cost_price must be a positive integer (kopecks)",
            },
            "name": {
                "missing": "name is required",
            },
            "value": {
                "missing": "value is required",
            },
            "items": {
                "missing": "items is required",
                "too_short": "At least one item is required",
                "value_error": "At least one item is required",
            },
            "sku_id": {
                "missing": "sku_id is required",
                "value_error": "sku_id must be a valid UUID",
                "string_pattern_mismatch": "sku_id must be a valid UUID",
            },
            "quantity": {
                "missing": "quantity is required",
                "value_error": "quantity must be > 0",
            },
        }

        if field in field_messages and msg_type in field_messages[field]:
            message = field_messages[field][msg_type]
        elif field in field_messages and "value_error" in field_messages[field]:
            message = field_messages[field]["value_error"]
        elif field in field_messages:
            message = field_messages[field].get("missing", first.get("msg", "Invalid value"))
        else:
            message = first.get("msg", "Invalid value")

        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"code": "INVALID_REQUEST", "message": message},
        )

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"code": "INVALID_REQUEST", "message": "Invalid request body"},
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": "ERROR", "message": str(exc.detail)},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="NeoMarket B2B Service",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)

app.include_router(products.router)
app.include_router(skus.router)
app.include_router(invoices.router)
app.include_router(public.router)
app.include_router(inventory.router)
app.include_router(moderation.router)
app.include_router(fulfill.router)
