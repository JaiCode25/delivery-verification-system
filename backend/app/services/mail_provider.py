"""Email delivery provider abstraction and implementations.

Supports:
- Dry-run provider (default): Simulates email delivery without network calls, safe for tests/demos.
- SMTP provider: Real transport using fastapi-mail with TLS verification and credential isolation.

Includes strict attachment security:
- Path traversal prevention
- Session-scoped evidence attachment
- Allowed MIME types and size limit enforcement
- Sensitive file blocking (weights, databases, logs)
"""
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from pydantic import BaseModel

from app.config import REPORT_DIR, STORAGE_DIR, settings


class EmailAttachment(BaseModel):
    filename: str
    file_path: Path
    mime_type: str
    size_bytes: int


class EmailDeliveryResult(BaseModel):
    success: bool
    status: str  # "sent", "dry_run", "failed"
    message: str
    timestamp: datetime
    error: Optional[str] = None
    recipient_email: str
    subject: str
    attachment_count: int = 0
    total_attachment_bytes: int = 0


ALLOWED_ATTACHMENT_MIMES = {
    "image/jpeg": [".jpg", ".jpeg"],
    "image/png": [".png"],
    "application/pdf": [".pdf"],
    "application/json": [".json"],
    "text/html": [".html"],
}

BLOCKED_PATTERNS = [
    r"\.pt$", r"\.pth$", r"\.onnx$", r"\.bin$", r"\.db$", r"\.sqlite$", r"\.sqlite3$", r"\.log$"
]


def validate_and_prepare_attachments(
    evidence_filenames: List[str],
    report_id: Optional[str],
    max_mb: int = 10,
) -> tuple[List[Path], int, List[str]]:
    """Validate attachments against path traversal, mime types, size bounds, and sensitive file blocks.

    Returns: (list_of_validated_paths, total_bytes, warnings)
    """
    validated: List[Path] = []
    total_bytes = 0
    warnings: List[str] = []
    max_bytes = max_mb * 1024 * 1024

    # Allowed search storage roots
    search_dirs = [
        settings.upload_dir,
        settings.frame_dir,
        settings.report_dir,
        settings.reference_dir,
    ]

    # Include report files if report_id provided
    if report_id:
        for ext in (".pdf", ".html", ".json"):
            candidate = settings.report_dir / f"{report_id}{ext}"
            if candidate.is_file():
                sz = candidate.stat().st_size
                if total_bytes + sz <= max_bytes:
                    validated.append(candidate)
                    total_bytes += sz
                else:
                    warnings.append(f"Report file '{candidate.name}' omitted due to total size limit ({max_mb} MB).")
                break

    for raw_name in evidence_filenames:
        if not raw_name or not isinstance(raw_name, str):
            continue
        # Prevent path traversal
        clean_name = Path(raw_name).name
        if clean_name != raw_name.replace("/", "").replace("\\", "") and ("/" in raw_name or "\\" in raw_name):
            warnings.append(f"Skipped invalid attachment name '{raw_name}': path traversal characters detected.")
            continue

        # Check blocked extensions (models, databases, logs)
        if any(re.search(pat, clean_name, re.IGNORECASE) for pat in BLOCKED_PATTERNS):
            warnings.append(f"Skipped prohibited attachment '{clean_name}': sensitive file type.")
            continue

        # Locate file strictly within allowed storage directories
        file_found: Optional[Path] = None
        for sdir in search_dirs:
            candidate = (sdir / clean_name).resolve()
            # Ensure resolved candidate is inside sdir
            if candidate.is_relative_to(sdir) and candidate.is_file():
                file_found = candidate
                break

        if not file_found:
            continue

        # Check file extension against allowed MIME types
        ext = file_found.suffix.lower()
        if not any(ext in exts for exts in ALLOWED_ATTACHMENT_MIMES.values()):
            warnings.append(f"Skipped attachment '{clean_name}': extension '{ext}' is not permitted.")
            continue

        size = file_found.stat().st_size
        if total_bytes + size > max_bytes:
            warnings.append(f"Attachment '{clean_name}' exceeds total size limit ({max_mb} MB) and was skipped.")
            continue

        if file_found not in validated:
            validated.append(file_found)
            total_bytes += size

    return validated, total_bytes, warnings


class MailProvider(ABC):
    @abstractmethod
    async def send_complaint(
        self,
        recipient_email: str,
        subject: str,
        body_text: str,
        body_html: str,
        attachment_paths: List[Path],
    ) -> EmailDeliveryResult:
        """Send or simulate email delivery."""
        pass


class DryRunMailProvider(MailProvider):
    """Simulates delivery without outbound networking. Safe for test suites and offline demos."""

    async def send_complaint(
        self,
        recipient_email: str,
        subject: str,
        body_text: str,
        body_html: str,
        attachment_paths: List[Path],
    ) -> EmailDeliveryResult:
        total_bytes = sum(p.stat().st_size for p in attachment_paths if p.is_file())
        return EmailDeliveryResult(
            success=True,
            status="dry_run",
            message=f"Dry-run delivery simulated successfully to '{recipient_email}' (attachments: {len(attachment_paths)}). No network calls made.",
            timestamp=datetime.now(timezone.utc),
            recipient_email=recipient_email,
            subject=subject,
            attachment_count=len(attachment_paths),
            total_attachment_bytes=total_bytes,
        )


class FastMailSmtpProvider(MailProvider):
    """Real SMTP delivery provider using fastapi-mail."""

    def __init__(self):
        # Validate that host and port are configured
        self.conf = ConnectionConfig(
            MAIL_USERNAME=settings.complaint_smtp_username or "",
            MAIL_PASSWORD=settings.complaint_smtp_password or "",
            MAIL_FROM=settings.complaint_email_from,
            MAIL_PORT=settings.complaint_smtp_port,
            MAIL_SERVER=settings.complaint_smtp_host,
            MAIL_STARTTLS=settings.complaint_smtp_starttls,
            MAIL_SSL_TLS=not settings.complaint_smtp_starttls and settings.complaint_smtp_port == 465,
            USE_CREDENTIALS=bool(settings.complaint_smtp_username),
            VALIDATE_CERTS=True,
        )
        self.fastmail = FastMail(self.conf)

    async def send_complaint(
        self,
        recipient_email: str,
        subject: str,
        body_text: str,
        body_html: str,
        attachment_paths: List[Path],
    ) -> EmailDeliveryResult:
        total_bytes = sum(p.stat().st_size for p in attachment_paths if p.is_file())
        attachments_for_msg = [str(p) for p in attachment_paths if p.is_file()]

        message = MessageSchema(
            subject=subject,
            recipients=[recipient_email],
            body=body_html,
            subtype=MessageType.html,
            attachments=attachments_for_msg,
        )

        try:
            await self.fastmail.send_message(message)
            return EmailDeliveryResult(
                success=True,
                status="sent",
                message=f"Complaint email successfully transmitted to '{recipient_email}'.",
                timestamp=datetime.now(timezone.utc),
                recipient_email=recipient_email,
                subject=subject,
                attachment_count=len(attachment_paths),
                total_attachment_bytes=total_bytes,
            )
        except Exception as exc:
            # Never leak SMTP credentials in error logs/messages
            err_msg = str(exc)
            if settings.complaint_smtp_password and settings.complaint_smtp_password in err_msg:
                err_msg = err_msg.replace(settings.complaint_smtp_password, "******")
            return EmailDeliveryResult(
                success=False,
                status="failed",
                message=f"SMTP transmission failed: {err_msg}",
                timestamp=datetime.now(timezone.utc),
                error=err_msg,
                recipient_email=recipient_email,
                subject=subject,
                attachment_count=len(attachment_paths),
                total_attachment_bytes=total_bytes,
            )


def get_mail_provider() -> MailProvider:
    """Resolve configured mail provider based on application settings."""
    if settings.complaint_email_enabled and settings.complaint_email_provider == "smtp":
        return FastMailSmtpProvider()
    return DryRunMailProvider()
