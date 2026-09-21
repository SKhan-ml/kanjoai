from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.database import get_db
from app.models import Vendor, PurchaseOrder
from app.schemas import PurchaseOrderCreate, PurchaseOrderOut, VendorCreate, VendorOut

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/vendors", response_model=VendorOut)
def create_vendor(payload: VendorCreate, db: Session = Depends(get_db)):
    existing = db.query(Vendor).filter(Vendor.name == payload.name).first()
    if existing:
        return existing
    vendor = Vendor(**payload.model_dump())
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return vendor


@router.get("/vendors", response_model=list[VendorOut])
def list_vendors(db: Session = Depends(get_db)):
    return db.query(Vendor).all()


@router.post("/purchase-orders", response_model=PurchaseOrderOut)
def create_purchase_order(payload: PurchaseOrderCreate, db: Session = Depends(get_db)):
    vendor = db.query(Vendor).filter(Vendor.name == payload.vendor_name).first()
    if not vendor:
        vendor = Vendor(name=payload.vendor_name)
        db.add(vendor)
        db.commit()
        db.refresh(vendor)

    existing = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == payload.po_number).first()
    if existing:
        raise HTTPException(400, f"PO '{payload.po_number}' already exists.")

    po = PurchaseOrder(
        po_number=payload.po_number,
        vendor_id=vendor.id,
        expected_amount=payload.expected_amount,
        currency=payload.currency,
    )
    db.add(po)
    db.commit()
    db.refresh(po)
    return po


@router.get("/purchase-orders", response_model=list[PurchaseOrderOut])
def list_purchase_orders(db: Session = Depends(get_db)):
    return db.query(PurchaseOrder).all()
