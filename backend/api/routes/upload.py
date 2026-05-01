"""
backend/api/routes/upload.py
─────────────────────────────────────────────────────────────
File upload ingestion endpoints.

POST /upload/file        → upload and process a transaction file
GET  /upload/logs        → upload history for current user
GET  /upload/logs/{id}   → detail on a specific upload

Processing pipeline per upload:
  1. Security validation (extension, MIME, size, path traversal)
  2. Duplicate file detection (SHA-256 file hash)
  3. Parser selection (CSV / Excel / PDF)
  4. Row-level parsing + normalization
  5. Batch categorization via AI engine
  6. Bulk insert with duplicate tracking
  7. Upload log update with final stats
  8. Temp file deletion
"""

from __future__ import annotations

import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.ai.categorizer import get_categorizer
from backend.api.routes.auth import get_current_user_or_guest as get_current_user
from backend.core.database import get_db
from backend.core.logger import get_logger
from backend.core.security import (
    compute_file_hash,
    compute_transaction_hash,
    validate_upload_file,
)
from backend.models.account import Account
from backend.models.transaction import Transaction
from backend.models.upload_log import UploadLog
from backend.models.user import User
from backend.services.file_parser.pdf_parser import PDFParser

log = get_logger(__name__)
router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────

class UploadResult(BaseModel):
    upload_log_id: int
    file_name: str
    status: str
    records_parsed: int
    records_inserted: int
    records_duplicate: int
    records_failed: int
    error_message: Optional[str]
    processing_time_ms: float


class UploadLogResponse(BaseModel):
    id: int
    file_name: str
    file_type: str
    file_size_bytes: int
    upload_date: datetime
    status: str
    records_parsed: int
    records_inserted: int
    records_duplicate: int
    records_failed: int
    error_message: Optional[str]
    account_id: Optional[int]

    model_config = {"from_attributes": True}


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/file", response_model=UploadResult)
async def upload_file(
    file: UploadFile = File(...),
    account_id: int = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadResult:
    """Upload and process a bank statement file."""
    start = time.perf_counter()

    # Verify account belongs to user
    account = db.query(Account).filter(
        Account.id == account_id,
        Account.user_id == current_user.id,
        Account.is_active == True,
    ).first()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {account_id} not found.",
        )

    # Read file bytes
    file_bytes = await file.read()
    file_size = len(file_bytes)

    # Security validation
    is_valid, error_msg = validate_upload_file(
        filename=file.filename or "unknown",
        file_content=file_bytes[:8],
        file_size=file_size,
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=error_msg,
        )

    # File-level duplicate check
    file_hash = compute_file_hash(file_bytes)
    existing = db.query(UploadLog).filter(
        UploadLog.user_id == current_user.id,
        UploadLog.file_hash == file_hash,
        UploadLog.status == "completed",
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This file was already uploaded on "
                f"{existing.upload_date.strftime('%Y-%m-%d %H:%M')}. "
                f"Upload ID: {existing.id}"
            ),
        )

    # Create upload log
    ext = Path(file.filename or "file.pdf").suffix.lstrip(".").lower()
    upload_log = UploadLog(
        user_id=current_user.id,
        account_id=account_id,
        file_name=file.filename or "unknown",
        file_type=ext,
        file_hash=file_hash,
        file_size_bytes=file_size,
        status="processing",
    )
    db.add(upload_log)
    db.flush()

    # Process file
    result = await _process_file(
        file_bytes=file_bytes,
        file_ext=ext,
        account_id=account_id,
        upload_log=upload_log,
        db=db,
    )

    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    db.commit()

    # Generate insights after successful upload
    if result["inserted"] > 0:
        try:
            from backend.ai.insight_engine import get_insight_engine

            engine = get_insight_engine()

            insight_count = engine.generate(
                user_id=current_user.id,
                db=db,
            )

            db.commit()

            log.info(
                "insight_engine.triggered",
                user_id=current_user.id,
                insights_generated=insight_count,
            )

        except Exception as e:
            log.error(
                "insight_engine.error",
                user_id=current_user.id,
                error=str(e),
            )

    # Sanitize filename before logging — prevent log injection
    safe_filename = (
        (file.filename or "unknown")
        .replace("\n", "")
        .replace("\r", "")
        .replace("\x1b", "")
        [:255]
    )
    log.info(
        "upload.complete",
        user_id=current_user.id,
        file=safe_filename,
        inserted=result["inserted"],
        duplicate=result["duplicate"],
        failed=result["failed"],
        ms=elapsed_ms,
    )

    return UploadResult(
        upload_log_id=upload_log.id,
        file_name=file.filename or "unknown",
        status=upload_log.status,
        records_parsed=result["parsed"],
        records_inserted=result["inserted"],
        records_duplicate=result["duplicate"],
        records_failed=result["failed"],
        error_message=upload_log.error_message,
        processing_time_ms=elapsed_ms,
    )


@router.get("/logs", response_model=List[UploadLogResponse])
async def get_upload_logs(
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[UploadLogResponse]:
    """Return paginated upload history for current user."""
    logs = (
        db.query(UploadLog)
        .filter(UploadLog.user_id == current_user.id)
        .order_by(UploadLog.upload_date.desc())
        .offset(offset)
        .limit(min(limit, 100))
        .all()
    )
    return [UploadLogResponse.model_validate(ul) for ul in logs]


@router.get("/logs/{log_id}", response_model=UploadLogResponse)
async def get_upload_log(
    log_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadLogResponse:
    """Get details for a specific upload."""
    upload_log = db.query(UploadLog).filter(
        UploadLog.id == log_id,
        UploadLog.user_id == current_user.id,
    ).first()
    if not upload_log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload log not found.",
        )
    return UploadLogResponse.model_validate(upload_log)


# ── Internal Pipeline ─────────────────────────────────────────────

async def _process_file(
    file_bytes: bytes,
    file_ext: str,
    account_id: int,
    upload_log: UploadLog,
    db: Session,
) -> dict:
    """
    Core file processing pipeline.
    Returns counts: parsed, inserted, duplicate, failed.
    """
    parsed = inserted = duplicate = failed = 0

    with tempfile.NamedTemporaryFile(
        suffix=f".{file_ext}", delete=False
    ) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)

    try:
        # PDF-only parser
        if file_ext != "pdf":
            upload_log.status = "failed"
            upload_log.error_message = f"Only PDF files are supported. Got: .{file_ext}"
            return {"parsed": 0, "inserted": 0, "duplicate": 0, "failed": 0}

        parser = PDFParser()

        # Parse rows
        rows = parser.parse(tmp_path)
        parsed = len(rows)
        upload_log.records_parsed = parsed

        if not rows:
            upload_log.status = "completed"
            upload_log.error_message = "No transactions found in file."
            return {"parsed": 0, "inserted": 0, "duplicate": 0, "failed": 0}

        # Batch categorize
        categorizer = get_categorizer()
        descriptions = [r.get("description", "") or "" for r in rows]
        categories = categorizer.batch_categorize(descriptions)

        # Build Transaction objects
        txn_objects = []
        hash_set = set()
        for row, cat in zip(rows, categories):
            try:
                tx_hash = compute_transaction_hash(
                    account_id=account_id,
                    date=str(row["date"]),
                    amount=float(row["amount"]),
                    description=row.get("description", ""),
                    transaction_type=row["type"],
                )
                # In-memory dedup — skip rows with duplicate hash
                if tx_hash in hash_set:
                    duplicate += 1
                    continue
                hash_set.add(tx_hash)
                txn_objects.append(Transaction(
                    account_id=account_id,
                    date=row["date"],
                    amount=abs(float(row["amount"])),
                    type=row["type"],
                    category=cat.category,
                    subcategory=cat.subcategory,
                    merchant=cat.merchant,
                    description=row.get("description", ""),
                    raw_description=row.get(
                        "raw_description", row.get("description", "")
                    ),
                    source="pdf",
                    hash=tx_hash,
                ))
            except Exception as e:
                log.warning("upload.tx.build_error", error=str(e))
                failed += 1

        # Raw executemany with INSERT OR IGNORE — fastest SQLite bulk path.
        # Bypasses SQLAlchemy ORM and dialect overhead entirely.
        # Duplicates silently skipped via OR IGNORE — no exceptions/rollbacks.
        SQL = """
            INSERT OR IGNORE INTO transactions
              (account_id, date, amount, type, category, subcategory,
               merchant, description, raw_description, source, hash, notes)
            VALUES
              (:account_id, :date, :amount, :type, :category, :subcategory,
               :merchant, :description, :raw_description, :source, :hash, :notes)
        """

        BATCH_SIZE = 1000
        tx_params = [
            {
                "account_id": tx.account_id,
                "date":        str(tx.date),
                "amount":      tx.amount,
                "type":        tx.type,
                "category":    tx.category,
                "subcategory": tx.subcategory,
                "merchant":    tx.merchant,
                "description": tx.description,
                "raw_description": tx.raw_description,
                "source":      tx.source,
                "hash":        tx.hash,
                "notes":       tx.notes,
            }
            for tx in txn_objects
        ]

        # Raw executemany in single transaction — fastest safe SQLite path.
        # INSERT OR IGNORE handles duplicates without exceptions/rollbacks.
        raw_conn = db.connection().connection

        try:
            before = raw_conn.execute(
                "SELECT total_changes()"
            ).fetchone()[0]
            raw_conn.executemany(SQL, tx_params)
            after = raw_conn.execute(
                "SELECT total_changes()"
            ).fetchone()[0]
            inserted = after - before
            duplicate = len(tx_params) - inserted
        except Exception as e:
            log.warning("upload.bulk.error", error=str(e))
            # Fall back to individual inserts to identify duplicates
            for params in tx_params:
                try:
                    raw_conn.execute(SQL, params)
                    inserted += 1
                except Exception:
                    duplicate += 1

        upload_log.records_inserted = inserted
        upload_log.records_duplicate = duplicate
        upload_log.records_failed = failed
        upload_log.status = "completed"

    except Exception as e:
        log.error("upload.pipeline.error", error=str(e), exc_info=True)
        upload_log.status = "failed"
        upload_log.error_message = str(e)[:500]
        failed = parsed

    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    return {
        "parsed": parsed,
        "inserted": inserted,
        "duplicate": duplicate,
        "failed": failed,
    }
