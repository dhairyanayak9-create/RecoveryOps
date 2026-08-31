# RecoveryOps final changes

Implemented in the Android app + backend:

1. Android connects to the configured Railway API URL.
2. Login supports username/email/mobile and binds one account to one Android device.
3. Dashboard shows assigned server total and local offline total.
4. Registration search is numeric-only, max 4 digits, auto-searches at 4 digits, and matches the last 4 registration digits.
5. Search is local-first for very fast offline/online results, with an online refresh when online.
6. Search result cards are compact, two-column, registration-number-only cards.
7. Vehicle details use a white background with black text and include a WhatsApp share button.
8. Online/OFFLINE mode is in the bottom bar; SYNC is also in the bottom bar.
9. Sync reports percentage + downloaded/total records and rebuilds the local offline dataset.
10. Authentication expiry/device removal now invalidates device-bound tokens.
11. ADMIN and OFFICE_STAFF can access the staff/admin modules; REPO_AGENT users do not see those modules in Android.
12. Backend search and sync access filtering was optimized to avoid per-record authorization queries.
13. Registration suffix is stored/indexed locally and on the server for faster 4-digit searches.
14. File import creates a notification record for every active user.
15. Android polls notifications and displays local notifications while the app is running/online.
16. App is not fullscreen; normal Android status bar remains visible.
17. Existing RecoveryOps app icon in `@drawable/recoveryops_icon` is preserved.
18. Web logout now calls the backend logout endpoint so device access is released.

## Important deployment note

The configured Railway URL is live, but this package does not automatically deploy code to Railway. The backend changes must be deployed to the Railway service before the live server uses them.

The Android debug APK was not rebuilt in this environment because the Gradle wrapper attempted to download Gradle from `services.gradle.org`, and this environment has no external network access. Open `android/` in Android Studio and build normally after the backend is deployed.

## Notification note

The package implements server-side notifications plus Android local notification polling. True push notifications while the app is completely closed require an FCM/Firebase project configuration; no Firebase credentials were present in the supplied project, so none were invented.


## v4 UI/data refinement
- Vehicle Details now shows BANK NAME.
- WhatsApp share includes BANK NAME.
- Android CRUD/action buttons are 64dp high with 14sp labels and padding.
- Vehicle confirmation/share button is 68dp high with 15sp label.
- Web vehicle details also shows bank name.
