from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import User

router = APIRouter()

@router.get("")
def list_users(db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.id.desc())).all()
    return [{"id": u.id, "username": u.username, "full_name": u.full_name, "email": u.email, "phone": u.phone, "role": u.role, "active": u.active} for u in users]
