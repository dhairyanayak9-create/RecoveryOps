# Login + Supabase agency compatibility fix

This package keeps the existing Supabase plural UUID agency schema intact and uses the existing singular RecoveryOps tables as the operational compatibility layer.

Key fixes:
- `user.agency_id` is added by the supplied non-destructive SQL migration.
- Existing `public.status_history` is NOT altered. RecoveryOps legacy status history uses `status_history_legacy`.
- `SUPER_ADMIN` is allowed to operate without an agency; tenant users remain agency-scoped.
- User creation under a SUPER_ADMIN requires an explicit `agency_id`.
- Email/mobile uniqueness remains enforced.

Run `SUPABASE_RECOVERYOPS_COMPAT_MIGRATION.sql` once in Supabase SQL Editor, then deploy this package to Railway.
