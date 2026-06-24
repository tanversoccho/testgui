"""Dikai Carton Printer FastAPI server bootstrap.

Run:
    cd server
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

API definitions live in routes.py. SQL lives in queries.py.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import db, lpn
from .config import settings
from .routes import router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dikai.server")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_pool()
    lpn.init_lpn_storage()
    logger.info(
        "Server ready. mode=%s rate_limit=%.1f/s burst=%d",
        "MOCK" if settings.USE_MOCK_DB else "ORACLE",
        settings.RATE_LIMIT_PER_SECOND,
        settings.RATE_LIMIT_BURST,
    )
    yield
    db.close_pool()
    logger.info("Server stopped.")


app = FastAPI(
    title="Dikai Carton Printer Server",
    description="REST gateway for the STM32 carton-printer fleet.",
    version="1.0",
    lifespan=lifespan,
)
app.include_router(router)


@app.middleware("http")
async def log_and_envelope(request: Request, call_next):
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as e:
        logger.exception("Unhandled exception: %s", e)
        return JSONResponse(
            status_code=500,
            content={"ok": False, "message": f"{type(e).__name__}: {e}"},
        )
    dt_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "%s %s -> %d (%.1f ms)",
        request.method,
        request.url.path,
        response.status_code,
        dt_ms,
    )
    return response


@app.exception_handler(HTTPException)
async def http_error_envelope(_request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "message": exc.detail},
    )
