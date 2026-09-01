# RecoveryOps COMPLETE checklist

## Website
- [x] Login by username/email/mobile
- [x] Dashboard
- [x] Client Management
- [x] Recovery Management
- [x] Chat Management
- [x] User Management
- [x] Bank Name Management
- [x] Two Wheeler Files
- [x] Four Wheeler Files
- [x] File Management
- [x] Data Search
- [x] File Upload / CSV / XLS / XLSX import
- [x] Reports
- [x] Agency Companies
- [x] Pay Your Challan
- [x] Notifications
- [x] Vehicle status workflow
- [x] Single and bulk vehicle delete
- [x] Delete confirmations
- [x] Admin-only destructive actions

## Users & access
- [x] Username unique
- [x] Email unique
- [x] Mobile unique
- [x] Email normalization
- [x] Indian mobile normalization
- [x] Deleted accounts cannot login
- [x] Deleted email/mobile remain reserved
- [x] Roles: ADMIN / REPO_AGENT / OFFICE_STAFF
- [x] Assignment-based data access
- [x] Read / status-update permissions
- [x] Admin-only user management

## Files/data
- [x] Imported file is tracked
- [x] File deletion removes its imported vehicle rows
- [x] Duplicate vehicle protection by registration/chassis/account
- [x] Vehicle source-file relationship
- [x] Vehicle detail
- [x] Vehicle status history
- [x] Location latitude/longitude fields
- [x] Offline sync API

## Voice search
- [x] Website microphone search
- [x] Android microphone search
- [x] Last-registration-digit search
- [x] Numeric digit recognition
- [x] English digit words
- [x] Hindi digit words
- [x] Bengali digit words
- [x] Gujarati digit words
- [x] Marathi digit words
- [x] Tamil digit words
- [x] Telugu digit words
- [x] Kannada digit words
- [x] Malayalam digit words
- [x] Unicode digit handling
- [x] Offline Android suffix search after DB sync

Voice recognition itself is provided by the device/browser speech service. The app normalizes recognized words into a digit and searches the local registration number suffix. "Any language" in practice depends on which language the installed speech-recognition service can recognize; the included normalizer covers the listed Indian languages plus English and numeric digits.

## Android
- [x] Login first
- [x] Live Railway API URL
- [x] Offline SQLite database
- [x] DB sync/download
- [x] Offline search
- [x] Voice search
- [x] User/session handling
- [x] Logout
- [x] APK build configuration

## Production
- [x] Railway DATABASE_URL support
- [x] MySQL/PyMySQL connection conversion
- [x] GitHub deployment layout
- [x] Production API HTTPS placeholder
- [x] APK signing instructions

## Important
- Do not commit `.env`, database passwords, JWT secrets, or Android signing keys to GitHub.
- Rotate the Railway MySQL password if it was previously exposed.
- For a fresh Railway database, `db.create_all()` can create the new tables. For an already-used production database, use migrations instead of relying on `create_all()` for schema changes.

- [x] Supabase PostgreSQL/psycopg connection support
- [x] Agency-scoped ORM read/write guard
- [x] `/api/health` database connectivity endpoint
