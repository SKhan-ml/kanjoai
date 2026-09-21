from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator

from app.models import InvoiceStatus


def _validate_currency(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    v = v.strip().upper()
    if len(v) != 3 or not v.isalpha():
        raise ValueError(f"'{v}' doesn't look like a 3-letter ISO currency code (e.g. JPY, USD).")
    return v


# ---- Vendors / POs -----------------------------------------------------

class VendorCreate(BaseModel):
    name: str
    tax_id: Optional[str] = None


class VendorOut(VendorCreate):
    id: int
    model_config = {"from_attributes": True}


class PurchaseOrderCreate(BaseModel):
    po_number: str = Field(min_length=1)
    vendor_name: str = Field(min_length=1)
    expected_amount: float = Field(gt=0, description="Must be a positive amount.")
    currency: str = "JPY"

    _check_currency = field_validator("currency")(_validate_currency)


class PurchaseOrderOut(BaseModel):
    id: int
    po_number: str
    vendor_id: int
    expected_amount: float
    currency: str
    model_config = {"from_attributes": True}


# ---- Extraction (what the vision step must return) ---------------------

class ExtractedInvoice(BaseModel):
    """Structured shape the agent forces the vision model's output into.
    Pydantic validation here is the first 'reliability' guardrail — a
    malformed or hallucinated response fails fast instead of silently
    corrupting the reconciliation.
    """
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    po_number: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = "JPY"
    invoice_date: Optional[str] = None
    confidence: float = Field(ge=0, le=1, default=0.5)
    notes: Optional[str] = None

    _check_currency = field_validator("currency")(_validate_currency)


# ---- Audit / Invoice out ------------------------------------------------

class AuditLogOut(BaseModel):
    id: int
    step: str
    model_used: Optional[str]
    detail: Optional[str]
    confidence: Optional[float]
    created_at: datetime
    model_config = {"from_attributes": True, "protected_namespaces": ()}


class InvoiceOut(BaseModel):
    id: int
    invoice_number: Optional[str]
    vendor_name_raw: Optional[str]
    po_number: Optional[str]
    purchase_order_id: Optional[int]
    amount: Optional[float]
    currency: Optional[str]
    invoice_date: Optional[str]
    status: InvoiceStatus
    decision_reason: Optional[str]
    confidence: Optional[float]
    created_at: datetime
    audit_logs: List[AuditLogOut] = []
    model_config = {"from_attributes": True}
