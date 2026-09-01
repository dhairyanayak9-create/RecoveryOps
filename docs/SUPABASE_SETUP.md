# Supabase PostgreSQL setup

RecoveryOps can run on Supabase PostgreSQL without removing or renaming the existing Supabase tables.

## 1. Get the database connection string

Supabase Dashboard -> Project Settings -> Database -> Connection string.

Set this in Railway/local environment:

`SUPABASE_DB_URL=postgresql://postgres:PASSWORD@HOST:5432/postgres?sslmode=require`

The application also accepts `DATABASE_URL` for compatibility.

## 2. Install dependencies

`pip install -r backend/requirements.txt`

The PostgreSQL driver is `psycopg[binary]`.

## 3. Start the backend

From the repository root:

`python backend/run.py`

Or from Railway with backend as the root directory:

`gunicorn -w 2 -b 0.0.0.0:$PORT "app:create_app()"`

## 4. Verify the database

Open:

`GET /api/health`

Expected response:

`{"status":"ok","database":"connected"}`

## 5. Agency isolation

The application uses its own SQLAlchemy compatibility tables (`agency_company`, `user`, `vehicle`, etc.) and stores `agency_id` on every tenant-owned record. The request user is resolved from the server-side user record, and SQLAlchemy tenant guards automatically constrain normal ORM reads and writes to that agency.

This is intentionally non-destructive: the existing Supabase tables such as `agencies`, `agency_members`, `profiles`, and `recovery_cases` are not dropped, renamed, or altered by application startup.

For a future migration to use the existing Supabase plural tables directly, do it as a separate migration after the current application has been validated; do not mix that migration with the deployment.
