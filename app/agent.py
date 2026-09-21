"""
The autonomous reconciliation agent.

This is deliberately NOT a single LLM call. It's a multi-step loop that
extracts, validates, decides and acts — writing an AuditLog row at every
step — because a one-shot classifier can't be escalated, can't explain
itself, and can't be trusted with financial approvals.

Pipeline:
  1. EXTRACT  — vision model reads the invoice image, returns structured
                fields (ExtractedInvoice). Escalates cheap->strong model
                automatically if confidence is low (see orcarouter_client).
  2. MATCH    — look up the PO the invoice claims to be against.
  3. VALIDATE — rule-based checks: PO exists, amount within tolerance,
                no duplicate invoice number, currency matches.
  4. DECIDE   — combine rule results + extraction confidence into a
                final status: auto_approved / flagged_for_review / rejected.
  5. ACT      — persist the decision and the full audit trail.
"""
import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Invoice, InvoiceStatus, PurchaseOrder, AuditLog
from app.schemas import ExtractedInvoice
from app.orcarouter_client import complete_json

logger = logging.getLogger("kanjoai.agent")

EXTRACTION_SYSTEM_PROMPT = (
    "You are an invoice-data-extraction engine. Given an invoice image, "
    "return ONLY a JSON object with keys: vendor_name, invoice_number, "
    "po_number, amount (number), currency (ISO code), invoice_date "
    "(YYYY-MM-DD if determinable), confidence (0-1, how sure you are the "
    "extraction is fully correct), notes (short string, anomalies you "
    "noticed such as blurry text, altered amounts, or missing fields). "
    "If a field is unreadable, use null rather than guessing."
)


def _log(db: Session, invoice: Invoice, step: str, detail: str,
         model_used: Optional[str] = None, confidence: Optional[float] = None):
    entry = AuditLog(
        invoice_id=invoice.id,
        step=step,
        model_used=model_used,
        detail=detail,
        confidence=confidence,
    )
    db.add(entry)
    db.commit()


def extract_invoice(db: Session, invoice: Invoice, image_url: str) -> ExtractedInvoice:
    result = complete_json(
        system_prompt=EXTRACTION_SYSTEM_PROMPT,
        user_prompt="Extract the invoice fields from the attached image.",
        schema=ExtractedInvoice,
        image_url=image_url,
    )

    attempt_summary = "; ".join(
        f"{a.model}: {'ok' if a.ok else 'failed (' + a.error[:120] + ')'}"
        for a in result.attempts
    )

    if result.parsed is None:
        _log(db, invoice, step="extraction", model_used=None,
             detail=f"All extraction attempts failed. {attempt_summary}", confidence=0.0)
        raise RuntimeError("Invoice extraction failed on every configured model.")

    _log(
        db, invoice, step="extraction",
        model_used=result.final_model,
        detail=(
            f"Extracted fields via {result.final_model}"
            f"{' (escalated from cheap model — low confidence)' if result.escalated else ''}. "
            f"Attempts: {attempt_summary}. Notes: {result.parsed.notes or 'none'}"
        ),
        confidence=result.parsed.confidence,
    )
    return result.parsed


def _match_purchase_order(db: Session, po_number: Optional[str]) -> Optional[PurchaseOrder]:
    if not po_number:
        return None
    return db.query(PurchaseOrder).filter(PurchaseOrder.po_number == po_number).first()


def _check_duplicate(db: Session, invoice: Invoice, invoice_number: Optional[str]) -> bool:
    if not invoice_number:
        return False
    dup = (
        db.query(Invoice)
        .filter(Invoice.invoice_number == invoice_number, Invoice.id != invoice.id)
        .first()
    )
    return dup is not None


def _normalize_name(name: str) -> str:
    """Lowercase, strip whitespace and common corporate suffixes so
    'Sample Vendor K.K.' and 'sample vendor' are recognised as the same
    vendor without needing an exact string match.
    """
    n = name.strip().lower()
    for suffix in ("k.k.", "kk", "inc.", "inc", "co.", "ltd.", "ltd",
                   "corp.", "corp", "llc", "株式会社", "有限会社"):
        n = n.replace(suffix, "")
    return " ".join(n.split())


def _vendor_name_matches(extracted_name: Optional[str], po_vendor_name: str) -> bool:
    """Cheap, dependency-free fuzzy match: after normalization, one name
    must contain the other, OR they must share most of their words. This
    catches the case that matters most for fraud/error detection — an
    invoice referencing the right PO number but issued by a *different*
    vendor than the one that PO was raised against.
    """
    if not extracted_name:
        return False
    a, b = _normalize_name(extracted_name), _normalize_name(po_vendor_name)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    a_words, b_words = set(a.split()), set(b.split())
    if not a_words or not b_words:
        return False
    overlap = len(a_words & b_words) / max(len(a_words), len(b_words))
    return overlap >= 0.5


def validate_and_decide(db: Session, invoice: Invoice, extracted: ExtractedInvoice) -> None:
    """Rule-based validation step. Every rule outcome is logged so a human
    reviewer can see exactly why the agent decided what it decided.
    """
    reasons: list[str] = []
    # Severity order matters: once an invoice is REJECTED, a later, milder
    # check (e.g. confidence) must never downgrade it back to FLAGGED or
    # AUTO_APPROVED. `_severity` makes status changes monotonic.
    severity = {InvoiceStatus.AUTO_APPROVED: 0, InvoiceStatus.FLAGGED: 1, InvoiceStatus.REJECTED: 2}
    status = InvoiceStatus.AUTO_APPROVED

    def escalate(new_status: InvoiceStatus, reason: str):
        nonlocal status
        reasons.append(reason)
        if severity[new_status] > severity[status]:
            status = new_status

    po = _match_purchase_order(db, extracted.po_number)
    if po is None:
        escalate(InvoiceStatus.FLAGGED, f"No matching purchase order found for PO '{extracted.po_number}'.")
    else:
        invoice.purchase_order_id = po.id

        if not _vendor_name_matches(extracted.vendor_name, po.vendor.name):
            # A correct PO number with a mismatched vendor name is a bigger
            # red flag than an amount typo — could be a forged/misdirected
            # invoice — so this rejects rather than merely flags.
            escalate(
                InvoiceStatus.REJECTED,
                f"Vendor name mismatch: invoice states '{extracted.vendor_name}', "
                f"but PO {po.po_number} was issued to '{po.vendor.name}'. "
                "Possible misdirected or fraudulent invoice.",
            )

        if extracted.amount is None:
            escalate(InvoiceStatus.FLAGGED, "Amount could not be extracted from the invoice.")
        elif extracted.amount <= 0:
            escalate(InvoiceStatus.FLAGGED, f"Extracted amount {extracted.amount} is not a valid positive number.")
        else:
            diff_pct = abs(extracted.amount - po.expected_amount) / max(po.expected_amount, 1) * 100
            if diff_pct > settings.auto_approve_tolerance_pct:
                escalate(
                    InvoiceStatus.FLAGGED,
                    f"Amount {extracted.amount} {extracted.currency} differs from PO "
                    f"expected {po.expected_amount} {po.currency} by {diff_pct:.1f}% "
                    f"(tolerance is {settings.auto_approve_tolerance_pct}%).",
                )
            if extracted.currency and po.currency and extracted.currency != po.currency:
                escalate(
                    InvoiceStatus.FLAGGED,
                    f"Currency mismatch: invoice is {extracted.currency}, PO is {po.currency}.",
                )

    if _check_duplicate(db, invoice, extracted.invoice_number):
        escalate(
            InvoiceStatus.REJECTED,
            f"Invoice number '{extracted.invoice_number}' already exists in the system (possible duplicate submission).",
        )

    if extracted.confidence < settings.confidence_escalation_threshold:
        escalate(
            InvoiceStatus.FLAGGED,
            f"Extraction confidence {extracted.confidence:.2f} is below the auto-approve threshold.",
        )

    if not reasons:
        reasons.append(
            f"Amount matches PO {po.po_number} within tolerance and extraction "
            f"confidence ({extracted.confidence:.2f}) is high — auto-approved with no human review needed."
        )

    invoice.status = status
    invoice.decision_reason = " ".join(reasons)
    db.commit()

    _log(
        db, invoice, step="decision", model_used=None,
        detail=f"Decision: {status.value}. {' '.join(reasons)}",
        confidence=extracted.confidence,
    )


def populate_invoice_fields(db: Session, invoice: Invoice, extracted: ExtractedInvoice) -> None:
    """Copy extracted fields onto the Invoice row and commit.

    Pulled out of run_reconciliation as its own step (rather than inlined)
    specifically so tests can call the exact same population logic before
    exercising validate_and_decide — duplicate-invoice-number detection
    depends on invoice.invoice_number actually being set first, and a test
    that skips this step would silently test the wrong thing.
    """
    invoice.vendor_name_raw = extracted.vendor_name
    invoice.invoice_number = extracted.invoice_number
    invoice.po_number = extracted.po_number
    invoice.amount = extracted.amount
    invoice.currency = extracted.currency
    invoice.invoice_date = extracted.invoice_date
    invoice.confidence = extracted.confidence
    invoice.raw_extracted_json = json.dumps(extracted.model_dump())
    db.commit()


def run_reconciliation(db: Session, invoice: Invoice, image_url: str) -> Invoice:
    """Top-level entry point: extract -> populate -> validate/decide."""
    extracted = extract_invoice(db, invoice, image_url)
    populate_invoice_fields(db, invoice, extracted)
    validate_and_decide(db, invoice, extracted)
    db.refresh(invoice)
    return invoice
