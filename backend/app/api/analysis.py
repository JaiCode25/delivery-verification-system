from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.analysis_service import analyze_media
from app.services.file_handler import save_upload

router = APIRouter(prefix="/api", tags=["Analysis"])


@router.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    saved_path, media_type = await save_upload(file)
    try:
        return analyze_media(saved_path, media_type, file.filename or saved_path.name)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:
        raise HTTPException(500, f"Analysis failed: {error}") from error
