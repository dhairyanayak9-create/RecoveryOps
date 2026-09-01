# RecoveryOps - production-ready foundation

## What is included
- Flask backend + SQLAlchemy
- Supabase PostgreSQL support through `SUPABASE_DB_URL` / `DATABASE_URL` (MySQL remains supported)
- Username/email/mobile login
- Server-side user access control
- Email and mobile uniqueness
- Soft-delete users: deleted users cannot log in and their email/mobile remain reserved
- Admin-only create/edit/delete user APIs
- Admin-only bank delete
- Admin-only vehicle single/bulk delete
- Excel/CSV import with duplicate vehicle detection
- Vehicle search and status updates
- Assignment-based data access
- Incremental Android sync API
- Android project for APK generation

## Local Windows run

1. Open CMD:
   `cd /d "C:\Users\Dhairya\Downloads\RecoveryOps_UPDATED"`
2. Create/activate a Python virtual environment if desired.
3. Install:
   `pip install -r backend/requirements.txt`
4. Copy `backend/.env.example` to `backend/.env` and set secrets/database.
5. Run:
   `python backend/run.py`
6. Open `http://127.0.0.1:5000`

Default seed admin (development only):
- username: `admin`
- password: `admin123`

Change/remove this before production.

## Railway

1. Create a backend service from this GitHub repository.
2. Set backend root directory to `/backend`.
3. For production, set `SUPABASE_DB_URL` to the PostgreSQL connection string from Supabase -> Project Settings -> Database.
4. Add strong random `SECRET_KEY` and `JWT_SECRET_KEY`.
5. Start command:
   `gunicorn -w 2 -b 0.0.0.0:$PORT "app:create_app()"`
7. Generate a Railway public domain.
8. Use the HTTPS domain as the Android/API base URL.

Do not put MySQL credentials in GitHub or inside the APK.

## Android APK

Open the `android` folder in Android Studio, let Gradle sync, then:
Build -> Generate App Bundle(s) / APK(s) -> Generate APK(s)

For production:
Build -> Generate Signed Bundle / APK -> APK -> create/use a release keystore.

Before building, set the Android API base URL to your Railway HTTPS domain.

## Supabase / agency tenancy

The backend uses PostgreSQL/SQLAlchemy with an agency-scoped schema. Every tenant model carries `agency_id`, authenticated requests establish the agency from the user record, and ORM SELECT/INSERT/UPDATE operations are guarded server-side so one agency cannot access another agency's records through normal API requests.

The existing Supabase `public.agencies`, `agency_members`, `profiles`, etc. tables are not deleted or renamed by the application. The RecoveryOps SQLAlchemy tables use the existing singular table names (`agency_company`, `user`, `vehicle`, etc.) so the application can move to the same Supabase PostgreSQL project without destructive schema changes.

Use `GET /api/health` after deployment to verify the backend can reach PostgreSQL.

## Important database note

`db.create_all()` is fine for a fresh database. It does not safely migrate an already-existing database when new columns/unique constraints are added. For a previously-created DB, use a migration (Alembic/Flask-Migrate) or recreate the fresh Railway DB.

## User uniqueness

The backend normalizes:
- email to lowercase
- Indian +91 phone formats to the same 10-digit value

It rejects duplicates with HTTP 409. The database also has UNIQUE constraints for email, phone, and username.

## Delete behavior

- User delete is a soft delete: login disabled, email/mobile remain reserved.
- Vehicle delete is permanent and admin-only.
- Bank delete is blocked when vehicles still reference that bank.
