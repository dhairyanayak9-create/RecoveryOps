# Business Platform

Reusable enterprise application foundation for Finance, Insurance, CRM, ERP and Recovery portals.

## Stack
- Python 3.12 + FastAPI
- SQLAlchemy 2 + PostgreSQL
- JWT authentication
- Next.js + TypeScript frontend
- Docker Compose for local development

## Run backend
cd business-platform/backend
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload

## Run frontend
cd business-platform/frontend
npm install
npm run dev

Set DATABASE_URL and JWT_SECRET in backend/.env.
