import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg import errors as pg

from .config import settings
from .db import close_pool, open_pool
from .routers import admin, auth, customer, manager, public, staff

log = logging.getLogger("flowq")


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_pool()
    yield
    close_pool()


app = FastAPI(
    title="flowQ API",
    description="Digital Queue & Appointment Management System. Log in with POST /api/auth/login, then click "
                "**Authorize** and paste the `access_token`.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(pg.UniqueViolation)
async def unique_violation(request: Request, exc: pg.UniqueViolation):
    return JSONResponse({"detail": "That already exists. Please use a different value."}, status_code=409)


@app.exception_handler(pg.ForeignKeyViolation)
async def fk_violation(request: Request, exc: pg.ForeignKeyViolation):
    return JSONResponse({"detail": "That item is linked to other records and can't be changed this way."}, status_code=409)


@app.exception_handler(pg.CheckViolation)
async def check_violation(request: Request, exc: pg.CheckViolation):
    return JSONResponse({"detail": "That value is not allowed."}, status_code=400)


@app.exception_handler(pg.OperationalError)
async def db_down(request: Request, exc: pg.OperationalError):
    log.exception("database error")
    return JSONResponse({"detail": "The database is not reachable right now. Please try again."}, status_code=503)


for r in (auth.router, public.router, customer.router, staff.router, manager.router, admin.router):
    app.include_router(r)

# The flowQ web pages are served from the same origin as the API: http://localhost:8000/
if settings.frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
