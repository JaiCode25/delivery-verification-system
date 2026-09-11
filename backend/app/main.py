from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analysis import router as analysis_router

app = FastAPI(
    title="Delivery Verification System - Phase 1",
    description="Upload a package image or video for barcode, QR-code and label-text analysis.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(analysis_router)


@app.get("/")
def home():
    return {"message": "Phase 1 backend is running", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "healthy"}
