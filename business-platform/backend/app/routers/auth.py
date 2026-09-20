from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import User
from ..security import create_token, hash_password, verify_password

router = APIRouter()

class Login(BaseModel):
    identifier: str
    password: str

class Register(BaseModel):
    username: str
    full_name: str
    email: EmailStr | None = None
    phone: str | None = None
    password: str

@router.post("/register")
def register(data: Register, db: Session = Depends(get_db)):
    existing = db.scalar(select(User).where(or_(User.username == data.username, User.email == data.email, User.phone == data.phone)))
    if existing:
        raise HTTPException(409, "Username, email or phone already registered")
    user = User(username=data.username, full_name=data.full_name, email=data.email, phone=data.phone, password_hash=hash_password(data.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "username": user.username}

@router.post("/login")
def login(data: Login, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(or_(User.username == data.identifier, User.email == data.identifier, User.phone == data.identifier)))
    if not user or not user.active or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Invalid ID or password")
    return {"access_token": create_token(user.id, user.role), "token_type": "bearer", "user": {"id": user.id, "username": user.username, "role": user.role}}
