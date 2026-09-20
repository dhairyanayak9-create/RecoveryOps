from fastapi import FastAPI
from .db import Base, engine
from .routers import auth, users, business

app = FastAPI(title="Business Platform API", version="1.0.0")

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

@app.get("/health")
def health():
    return {"status": "ok"}

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(business.router, prefix="/api/v1", tags=["business"])
