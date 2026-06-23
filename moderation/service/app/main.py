from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status

from .routers import tickets


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = first.get("loc", ())
        field = loc[-1] if loc else "body"
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
    yield


app = FastAPI(
    title="NeoMarket Moderation Service",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)

app.include_router(tickets.router)
