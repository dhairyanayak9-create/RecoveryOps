# Full installation & APK process

## A. Supabase PostgreSQL (recommended)
1. Create/use the Supabase project.
2. Copy the PostgreSQL connection string from Project Settings → Database.
3. Set `SUPABASE_DB_URL` with `sslmode=require`.
4. Verify `/api/health` before connecting Android.

## B. MySQL (legacy compatibility)
1. Install MySQL 8.
2. Run database/schema.sql.
3. Set DATABASE_URL.
4. Start backend.

## B. Website
1. Open `http://SERVER:5000`.
2. Login.
3. Add banks.
4. Upload Excel.
5. Verify duplicate/import counts.
6. Create users and assignments (API/admin module can be extended for creation UI).

## C. Android
1. Install Android Studio.
2. Open `android/`.
3. Wait for Gradle sync.
4. Start backend.
5. Enter API URL.
6. Login.
7. Test sync.
8. Test with airplane mode.
9. For production, replace the temporary foundation with Room persistence and encrypted storage, then run full acceptance tests.

## D. Signed APK
Android Studio → Build → Generate Signed Bundle / APK → APK → Create new keystore → release → Finish.
The generated APK will normally be under:
`android/app/build/outputs/apk/release/`

## E. Distribution
For direct installation, send the signed APK to users.
For Play Store, generate an AAB and upload it through Google Play Console.

## Security before real use
- Replace default credentials.
- Use HTTPS.
- Store secrets outside source code.
- Restrict CORS.
- Do not put Aadhaar/PAN or other sensitive documents into offline storage unless strictly necessary and encrypted.
- Add backups, monitoring, rate limits, audit review, and load tests.
