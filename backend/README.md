# Delivery Verification System — Phase 1

This FastAPI backend accepts package images and videos, extracts one frame per
second from videos, scans QR codes/barcodes, extracts English label text, and
saves a JSON analysis report.

## Windows setup

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs`, expand `POST /api/analyze`, click **Try it
out**, select an image or short video, and click **Execute**.

The first analysis downloads EasyOCR's pretrained model. Generated files are
stored under `storage/` and are excluded from Git.
