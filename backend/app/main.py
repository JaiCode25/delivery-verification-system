from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analysis import router as analysis_router
from app.api.orders import router as orders_router
from app.api.product_matching import router as product_matching_router
from app.api.reports import router as reports_router
from app.api.verification import router as verification_router
from app.api.quantity_verification import router as quantity_router
from app.api.condition_analysis import router as condition_router
from app.api.customer_submissions import router as customer_submissions_router
from app.api.complaints import router as complaints_router
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
    title="Delivery Verification System",
    description="Package media analysis, barcode decoding, label OCR, hybrid product matching, customer intake, and complaint assistant.",
    version="1.4.0",
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
app.include_router(product_matching_router)
app.include_router(reports_router)
app.include_router(quantity_router)
app.include_router(condition_router)
app.include_router(customer_submissions_router)
app.include_router(complaints_router)


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
