import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Vendor, PurchaseOrder


@pytest.fixture()
def db_session():
    """A fresh in-memory SQLite DB per test — fast, isolated, no fixtures
    leaking between tests.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def vendor(db_session):
    v = Vendor(name="Sample Vendor K.K.", tax_id="T1234567890123")
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v


@pytest.fixture()
def purchase_order(db_session, vendor):
    po = PurchaseOrder(po_number="PO-1001", vendor_id=vendor.id, expected_amount=128000.0, currency="JPY")
    db_session.add(po)
    db_session.commit()
    db_session.refresh(po)
    return po
