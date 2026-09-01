# Architecture

Browser / Android → Flask REST API → Supabase PostgreSQL (or MySQL/SQLite for compatibility)
Android → Flask REST API → Room/SQLite local cache

Offline:
Android writes allowed status changes to a local queue.
When online, POST `/api/sync/mutations`.
Each mutation has a unique ID; the server ignores a duplicate ID, preventing double application.

Incremental download:
GET `/api/sync/download?after_version=N&limit=500`
Only records with a newer version are returned.
