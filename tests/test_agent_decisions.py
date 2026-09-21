"""
Unit tests for the agent's rule-based decision logic (app.agent.validate_and_decide).

These deliberately do NOT call OrcaRouter — they construct an
ExtractedInvoice directly, as if extraction had already happened, and
assert on the resulting InvoiceStatus + decision_reason. That keeps the
suite fast and offline, and it's what caught the vendor-mismatch severity
bug below before it shipped.
"""
import pytest

from app.agent import validate_and_decide, populate_invoice_fields, _vendor_name_matches, _normalize_name
from app.models import Invoice, InvoiceStatus
from app.schemas import ExtractedInvoice


def make_invoice(db_session) -> Invoice:
    inv = Invoice(status=InvoiceStatus.PENDING)
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv


def decide(db_session, invoice: Invoice, extracted: ExtractedInvoice) -> None:
    """Mirrors app.agent.run_reconciliation's populate-then-decide order,
    so tests can't drift from what production actually does (see
    populate_invoice_fields's docstring for why this matters).
    """
    populate_invoice_fields(db_session, invoice, extracted)
    validate_and_decide(db_session, invoice, extracted)


def test_auto_approves_when_everything_matches(db_session, purchase_order):
    invoice = make_invoice(db_session)
    extracted = ExtractedInvoice(
        vendor_name="Sample Vendor K.K.",
        invoice_number="INV-001",
        po_number="PO-1001",
        amount=128000.0,
        currency="JPY",
        confidence=0.95,
    )
    decide(db_session, invoice, extracted)
    assert invoice.status == InvoiceStatus.AUTO_APPROVED


def test_flags_when_amount_exceeds_tolerance(db_session, purchase_order):
    invoice = make_invoice(db_session)
    extracted = ExtractedInvoice(
        vendor_name="Sample Vendor K.K.",
        invoice_number="INV-002",
        po_number="PO-1001",
        amount=150000.0,  # >2% over the PO's 128000
        currency="JPY",
        confidence=0.95,
    )
    decide(db_session, invoice, extracted)
    assert invoice.status == InvoiceStatus.FLAGGED
    assert "differs from PO" in invoice.decision_reason


def test_auto_approves_within_tolerance(db_session, purchase_order):
    invoice = make_invoice(db_session)
    extracted = ExtractedInvoice(
        vendor_name="Sample Vendor K.K.",
        invoice_number="INV-003",
        po_number="PO-1001",
        amount=128500.0,  # ~0.4% over — within the 2% default tolerance
        currency="JPY",
        confidence=0.95,
    )
    decide(db_session, invoice, extracted)
    assert invoice.status == InvoiceStatus.AUTO_APPROVED


def test_rejects_on_vendor_mismatch(db_session, purchase_order):
    invoice = make_invoice(db_session)
    extracted = ExtractedInvoice(
        vendor_name="Totally Different Company Inc.",
        invoice_number="INV-004",
        po_number="PO-1001",
        amount=128000.0,
        currency="JPY",
        confidence=0.95,
    )
    decide(db_session, invoice, extracted)
    assert invoice.status == InvoiceStatus.REJECTED
    assert "Vendor name mismatch" in invoice.decision_reason


def test_flags_when_po_not_found(db_session, purchase_order):
    invoice = make_invoice(db_session)
    extracted = ExtractedInvoice(
        vendor_name="Sample Vendor K.K.",
        invoice_number="INV-005",
        po_number="PO-9999-DOES-NOT-EXIST",
        amount=128000.0,
        currency="JPY",
        confidence=0.95,
    )
    decide(db_session, invoice, extracted)
    assert invoice.status == InvoiceStatus.FLAGGED
    assert "No matching purchase order" in invoice.decision_reason


def test_rejects_duplicate_invoice_number(db_session, purchase_order):
    first = make_invoice(db_session)
    extracted = ExtractedInvoice(
        vendor_name="Sample Vendor K.K.", invoice_number="INV-DUP",
        po_number="PO-1001", amount=128000.0, currency="JPY", confidence=0.95,
    )
    decide(db_session, first, extracted)
    assert first.status == InvoiceStatus.AUTO_APPROVED

    second = make_invoice(db_session)
    decide(db_session, second, extracted)  # same invoice_number
    assert second.status == InvoiceStatus.REJECTED
    assert "already exists" in second.decision_reason


def test_flags_low_confidence_even_if_everything_else_matches(db_session, purchase_order):
    invoice = make_invoice(db_session)
    extracted = ExtractedInvoice(
        vendor_name="Sample Vendor K.K.",
        invoice_number="INV-006",
        po_number="PO-1001",
        amount=128000.0,
        currency="JPY",
        confidence=0.4,  # below default threshold of 0.75
    )
    decide(db_session, invoice, extracted)
    assert invoice.status == InvoiceStatus.FLAGGED
    assert "confidence" in invoice.decision_reason.lower()


def test_rejection_is_not_downgraded_by_a_later_milder_check(db_session, purchase_order):
    """Regression test: a vendor mismatch (REJECTED) must not be
    overwritten by a later, less severe check such as low confidence.
    """
    invoice = make_invoice(db_session)
    extracted = ExtractedInvoice(
        vendor_name="Totally Different Company Inc.",
        invoice_number="INV-007",
        po_number="PO-1001",
        amount=128000.0,
        currency="JPY",
        confidence=0.4,  # also low confidence — should NOT downgrade REJECTED to FLAGGED
    )
    decide(db_session, invoice, extracted)
    assert invoice.status == InvoiceStatus.REJECTED


def test_flags_missing_amount(db_session, purchase_order):
    invoice = make_invoice(db_session)
    extracted = ExtractedInvoice(
        vendor_name="Sample Vendor K.K.", invoice_number="INV-008",
        po_number="PO-1001", amount=None, currency="JPY", confidence=0.95,
    )
    decide(db_session, invoice, extracted)
    assert invoice.status == InvoiceStatus.FLAGGED
    assert "could not be extracted" in invoice.decision_reason


@pytest.mark.parametrize("a,b,expected", [
    ("Sample Vendor K.K.", "Sample Vendor K.K.", True),
    ("sample vendor", "Sample Vendor K.K.", True),
    ("Sample Vendor", "Sample Vendor Co., Ltd.", True),
    ("Acme Corp", "Sample Vendor K.K.", False),
    (None, "Sample Vendor K.K.", False),
])
def test_vendor_name_matches(a, b, expected):
    assert _vendor_name_matches(a, b) is expected


def test_normalize_name_strips_suffixes():
    assert _normalize_name("Sample Vendor K.K.") == _normalize_name("sample vendor")
