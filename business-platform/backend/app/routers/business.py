from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import Bank, Customer, Vehicle, RecoveryCase

router = APIRouter()

class BankIn(BaseModel):
    name: str
    branch: str | None = None

class CustomerIn(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    address: str | None = None

class VehicleIn(BaseModel):
    registration_no: str | None = None
    loan_no: str | None = None
    customer_id: int | None = None
    bank_id: int | None = None

class CaseIn(BaseModel):
    vehicle_id: int | None = None
    customer_id: int | None = None
    status: str = "PENDING"
    priority: str = "NORMAL"
    amount: float | None = None
    notes: str | None = None

@router.post("/banks")
def create_bank(data: BankIn, db: Session = Depends(get_db)):
    row = Bank(**data.model_dump()); db.add(row); db.commit(); db.refresh(row); return row

@router.get("/banks")
def banks(db: Session = Depends(get_db)):
    return db.scalars(select(Bank).order_by(Bank.id.desc())).all()

@router.post("/customers")
def create_customer(data: CustomerIn, db: Session = Depends(get_db)):
    row = Customer(**data.model_dump()); db.add(row); db.commit(); db.refresh(row); return row

@router.get("/customers")
def customers(db: Session = Depends(get_db)):
    return db.scalars(select(Customer).order_by(Customer.id.desc())).all()

@router.post("/vehicles")
def create_vehicle(data: VehicleIn, db: Session = Depends(get_db)):
    row = Vehicle(**data.model_dump()); db.add(row); db.commit(); db.refresh(row); return row

@router.get("/vehicles")
def vehicles(db: Session = Depends(get_db)):
    return db.scalars(select(Vehicle).order_by(Vehicle.id.desc())).all()

@router.post("/recovery-cases")
def create_case(data: CaseIn, db: Session = Depends(get_db)):
    row = RecoveryCase(**data.model_dump()); db.add(row); db.commit(); db.refresh(row); return row

@router.get("/recovery-cases")
def cases(db: Session = Depends(get_db)):
    return db.scalars(select(RecoveryCase).order_by(RecoveryCase.id.desc())).all()
