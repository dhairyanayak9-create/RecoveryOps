RecoveryOps Final Android package

Behavior:
- Login is required before any search.
- Search exists only on the Data Search page.
- Registration number only; no chassis/account/customer/mobile search.
- Maximum 4 characters; 5th character cannot be entered.
- Search automatically starts at exactly 4 characters.
- Results appear as cards; tap a card for complete raw-record details.
- One tap on DOWNLOAD ALL RECORDS OFFLINE automatically paginates through the entire accessible server dataset until has_more=false.
- The app stores records in SQLite for offline search.
- Server URL is hidden from the UI.

The backend /api/sync/download endpoint in this package uses (version,id) cursor pagination so datasets where many rows share the same version do not stop at 2,000 records.

Build (Windows / Android Studio JBR):
1. Open this folder in Android Studio.
2. Use the bundled Gradle wrapper (Gradle 8.10).
3. PowerShell from the android folder:
   & "C:\Program Files\Android\Android Studio\jbr\bin\java.exe" -cp ".\gradle\wrapper\gradle-wrapper.jar" org.gradle.wrapper.GradleWrapperMain :app:assembleDebug
4. APK output: android\app\build\outputs\apk\debug\app-debug.apk
