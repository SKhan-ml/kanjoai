import base64
import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.database import get_db
from app.models import Invoice, InvoiceStatus
from app.schemas import InvoiceOut
from app.agent import run_reconciliation

logger = logging.getLogger("kanjoai.invoices")

router = APIRouter(dependencies=[Depends(require_api_key)])

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}


@router.post("/invoices/process", response_model=InvoiceOut)
async def process_invoice(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload an invoice image; the agent extracts it, reconciles it
    against purchase orders, and returns the final autonomous decision
    plus the full step-by-step audit trail.
    """
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, f"Unsupported file type '{file.content_type}'. Upload a PNG/JPEG/WebP image.")

    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 8MB).")

    image_url = f"data:{file.content_type};base64,{base64.b64encode(raw).decode()}"

    invoice = Invoice(source_filename=file.filename, status=InvoiceStatus.PENDING)
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    try:
        invoice = run_reconciliation(db, invoice, image_url)
    except Exception as e:
        logger.exception("Reconciliation failed for invoice %s", invoice.id)
        invoice.status = InvoiceStatus.FLAGGED
        invoice.decision_reason = f"Agent error, routed to human review: {e}"
        db.commit()
        db.refresh(invoice)

    return invoice


@router.get("/invoices", response_model=list[InvoiceOut])
def list_invoices(status_filter: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Invoice)
    if status_filter:
        q = q.filter(Invoice.status == status_filter)
    return q.order_by(Invoice.created_at.desc()).all()


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(Invoice).get(invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found.")
    return invoice
