from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analysis import router as analysis_router
from app.api.orders import router as orders_router
from app.api.verification import router as verification_router
from app.config import settings
from app.database.connection import init_db
from app.database.seed import seed_demonstration_orders


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to initialize database tables and seed demo data."""
    init_db()
    seed_demonstration_orders()
    yield


app = FastAPI(
    title="Delivery Verification System - Phase 1",
    description="Package media analysis, barcode decoding, label OCR, and SQLite order verification.",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analysis_router)
app.include_router(orders_router)
app.include_router(verification_router)


@app.get("/")
def home():
    return {
        "message": "Delivery Verification System backend is running",
        "docs": "/docs",
        "version": "1.1.0",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}
