"""
Seeds a vendor + PO so a demo invoice can auto-reconcile end to end.
Run: python -m scripts.seed_demo_data
"""
from app.database import SessionLocal, init_db
from app.models import Vendor, PurchaseOrder


def main():
    init_db()
    db = SessionLocal()
    try:
        vendor = db.query(Vendor).filter(Vendor.name == "Sample Vendor K.K.").first()
        if not vendor:
            vendor = Vendor(name="Sample Vendor K.K.", tax_id="T1234567890123")
            db.add(vendor)
            db.commit()
            db.refresh(vendor)
            print(f"Created vendor: {vendor.name} (id={vendor.id})")

        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == "PO-1001").first()
        if not po:
            po = PurchaseOrder(
                po_number="PO-1001",
                vendor_id=vendor.id,
                expected_amount=128000.0,
                currency="JPY",
            )
            db.add(po)
            db.commit()
            print(f"Created PO: {po.po_number} for JPY {po.expected_amount}")
        else:
            print(f"PO {po.po_number} already exists.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
