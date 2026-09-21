"""
SQLAlchemy tables.

The AuditLog table is what makes every autonomous decision explainable
after the fact — which model was used, what it saw, what it decided, and
why. Without this, "the agent auto-approved it" is not something anyone
downstream (finance, an auditor, a future version of me) could trust.
"""
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Text, Enum
)
from sqlalchemy.orm import relationship

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class InvoiceStatus(str, enum.Enum):
    PENDING = "pending"
    AUTO_APPROVED = "auto_approved"
    FLAGGED = "flagged_for_review"
    REJECTED = "rejected"


class Vendor(Base):
    __tablename__ = "vendors"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    tax_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    purchase_orders = relationship("PurchaseOrder", back_populates="vendor")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True)
    po_number = Column(String, nullable=False, unique=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    expected_amount = Column(Float, nullable=False)
    currency = Column(String, default="JPY")
    created_at = Column(DateTime, default=utcnow)

    vendor = relationship("Vendor", back_populates="purchase_orders")
    invoices = relationship("Invoice", back_populates="purchase_order")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)
    invoice_number = Column(String, nullable=True, index=True)
    vendor_name_raw = Column(String, nullable=True)  # as extracted, before matching
    po_number = Column(String, nullable=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=True)

    amount = Column(Float, nullable=True)
    currency = Column(String, nullable=True)
    invoice_date = Column(String, nullable=True)

    source_filename = Column(String, nullable=True)
    raw_extracted_json = Column(Text, nullable=True)  # full structured extraction, for audit

    status = Column(Enum(InvoiceStatus), default=InvoiceStatus.PENDING, nullable=False)
    decision_reason = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    purchase_order = relationship("PurchaseOrder", back_populates="invoices")
    audit_logs = relationship("AuditLog", back_populates="invoice", order_by="AuditLog.id")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)

    step = Column(String, nullable=False)       # e.g. "extraction", "validation", "decision", "escalation"
    model_used = Column(String, nullable=True)   # which OrcaRouter model handled this step
    detail = Column(Text, nullable=True)          # human-readable reasoning / payload summary
    confidence = Column(Float, nullable=True)

    created_at = Column(DateTime, default=utcnow)

    invoice = relationship("Invoice", back_populates="audit_logs")
