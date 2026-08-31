package com.recoveryops;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.ContentValues;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.database.Cursor;
import android.provider.OpenableColumns;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.text.Editable;
import android.text.InputFilter;
import android.text.InputType;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.InputMethodManager;
import android.widget.*;

import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.NotificationCompat;
import androidx.drawerlayout.widget.DrawerLayout;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okhttp3.MultipartBody;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.Locale;
import java.util.concurrent.TimeUnit;

public class MainActivity extends AppCompatActivity {

    static final String SERVER_URL =
            "https://recoveryops-production-56b0.up.railway.app";

    private static final MediaType JSON =
            MediaType.parse("application/json; charset=utf-8");

    private static final int AUDIO_REQ = 501;
    private static final int NOTIFICATION_REQ = 502;
    private static final int FILE_PICK_REQ = 503;

    private static final String NOTIFICATION_CHANNEL =
            "recoveryops_updates";

    private EditText username;
    private EditText password;
    private EditText search;
    private EditText dashboardSearch;

    private TextView loginResult;
    private TextView resultMain;
    private TextView resultCount;
    private TextView welcome;
    private TextView stats;
    private TextView syncStatus;
    private TextView totalStat;
    private TextView activityText;
    private TextView lastSyncText;
    private TextView drawerRole;
    private TextView moduleMessage;

    private GridLayout resultList;

    private View loginPanel;
    private View appPanel;
    private View dashboardPage;
    private View searchPage;

    /*
     * IMPORTANT:
     * This must be ViewGroup because modulePage contains dynamically
     * generated child views and we use addView/removeViews on it.
     */
    private ViewGroup modulePage;

    private SharedPreferences prefs;
    private LocalDb localDb;

    private final OkHttpClient client = new OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build();

    private final Handler handler =
            new Handler(Looper.getMainLooper());

    private boolean online = true;
    private boolean syncing = false;
    private boolean syncReceiverRegistered = false;
    private String currentPageKey = "home";
    private int currentModuleId = 0;
    private String currentModuleTitle = "";
    private final ArrayList<PageState> pageHistory = new ArrayList<>();
    private boolean voiceMode = false;
    private boolean suppressSearchWatcher = false;

    private int syncTotal = 0;
    private int syncDownloaded = 0;

    private final Runnable notificationPoll = new Runnable() {
        @Override
        public void run() {
            if (isLoggedIn() && online) {
                pollNotifications();
            }

            handler.postDelayed(this, 15000);
        }
    };

    private static class PageState {
        final String key;
        final int moduleId;
        final String title;

        PageState(String key, int moduleId, String title) {
            this.key = key;
            this.moduleId = moduleId;
            this.title = title;
        }
    }

    private final BroadcastReceiver syncReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (!SyncService.ACTION_PROGRESS.equals(intent.getAction())) return;

            int pct = intent.getIntExtra(SyncService.EXTRA_PERCENT, -1);
            int done = intent.getIntExtra(SyncService.EXTRA_DONE, 0);
            boolean complete = intent.getBooleanExtra(SyncService.EXTRA_COMPLETE, false);

            if (pct == -2) {
                syncing = false;
                return;
            }

            if (pct < 0) {
                syncing = false;
                if (syncStatus != null) syncStatus.setText("Sync failed");
                return;
            }

            syncing = !complete;
            if (syncStatus != null) {
                syncStatus.setText(
                        complete ? "Sync complete • 100%" : "Syncing… " + pct + "%"
                );
            }

            if (stats != null) {
                stats.setText(done + " records");
            }

            if (complete && totalStat != null && localDb != null) {
                totalStat.setText(String.valueOf(localDb.count()));
                updateLastSyncText();
                if (activityText != null) {
                    activityText.setText("Offline database updated");
                }
            }
        }
    };

    private void registerSyncReceiver() {
        if (syncReceiverRegistered) return;
        IntentFilter filter = new IntentFilter(SyncService.ACTION_PROGRESS);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(syncReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(syncReceiver, filter);
        }
        syncReceiverRegistered = true;
    }

    private void unregisterSyncReceiver() {
        if (!syncReceiverRegistered) return;
        try {
            unregisterReceiver(syncReceiver);
        } catch (Exception ignored) {
        }
        syncReceiverRegistered = false;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        getWindow().setStatusBarColor(Color.rgb(145, 145, 150));
        getWindow().setNavigationBarColor(Color.rgb(31, 28, 44));
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
        );

        setContentView(R.layout.activity_main);

        initViews();

        prefs = getSharedPreferences(
                "recoveryops",
                MODE_PRIVATE
        );

        localDb = new LocalDb(this);
        online = prefs.getBoolean("online_mode", true);

        createNotificationChannel();
        requestNotificationPermissionIfNeeded();
        initListeners();

        setupSearch(search);
        setupSearch(dashboardSearch);

        if (hasToken()) {
            showApp();
        } else {
            showLogin();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();

        registerSyncReceiver();
        handler.removeCallbacks(notificationPoll);
        handler.post(notificationPoll);
    }

    @Override
    protected void onPause() {
        super.onPause();

        unregisterSyncReceiver();
        handler.removeCallbacks(notificationPoll);
    }

    private void initViews() {

        username = findViewById(R.id.username);
        password = findViewById(R.id.password);

        search = findViewById(R.id.search);
        dashboardSearch = findViewById(R.id.dashboardSearch);

        loginResult = findViewById(R.id.result);
        resultMain = findViewById(R.id.resultMain);
        resultCount = findViewById(R.id.resultCount);
        resultList = findViewById(R.id.resultList);

        welcome = findViewById(R.id.welcome);
        stats = findViewById(R.id.stats);
        syncStatus = findViewById(R.id.syncStatus);
        totalStat = findViewById(R.id.totalStat);
        activityText = findViewById(R.id.activityText);
        lastSyncText = findViewById(R.id.lastSyncText);
        drawerRole = findViewById(R.id.drawerRole);
        moduleMessage = findViewById(R.id.moduleMessage);

        loginPanel = findViewById(R.id.loginPanel);
        appPanel = findViewById(R.id.appPanel);
        dashboardPage = findViewById(R.id.dashboardPage);
        searchPage = findViewById(R.id.searchPage);

        /*
         * Cast once here so all later addView/removeViews operations
         * compile correctly.
         */
        modulePage = findViewById(R.id.modulePage);
    }

    private void initListeners() {

        findViewById(R.id.login).setOnClickListener(
                v -> login()
        );

        findViewById(R.id.sync).setOnClickListener(
                v -> syncAll()
        );

        findViewById(R.id.menuBtn).setOnClickListener(v -> {

            DrawerLayout drawer =
                    findViewById(R.id.drawer);

            if (drawer != null) {
                drawer.openDrawer(Gravity.LEFT);
            }
        });

        findViewById(R.id.logoutBtn).setOnClickListener(
                v -> logoutNow()
        );

        findViewById(R.id.modeBtn).setOnClickListener(
                v -> toggleMode()
        );

        findViewById(R.id.voiceBtn).setOnClickListener(v -> {

            if (isLoggedIn()) {
                startVoice();
            } else {
                showLogin();
            }
        });

        findViewById(R.id.bottomHome).setOnClickListener(
                v -> showHome()
        );

        findViewById(R.id.bottomProfile).setOnClickListener(
                v -> showProfile()
        );

        int[] nav = {
                R.id.navDashboard,
                R.id.navSearch,
                R.id.navTwo,
                R.id.navFour,
                R.id.navCv,
                R.id.navFiles,
                R.id.navBanks,
                R.id.navClients,
                R.id.navUsers,
                R.id.navReports,
                R.id.navNotifications
        };

        for (int id : nav) {

            View view = findViewById(id);

            if (view != null) {

                view.setOnClickListener(v ->
                        navigate(
                                v.getId(),
                                v instanceof Button
                                        ? ((Button) v).getText().toString()
                                        : "Module"
                        )
                );
            }
        }
    }

    private void setupSearch(EditText field) {

        if (field == null) {
            return;
        }

        field.setInputType(
                InputType.TYPE_CLASS_NUMBER |
                        InputType.TYPE_NUMBER_VARIATION_NORMAL
        );

        field.setKeyListener(
                android.text.method.DigitsKeyListener
                        .getInstance("0123456789")
        );

        field.setFilters(
                new InputFilter[]{
                        new InputFilter.LengthFilter(4)
                }
        );

        field.setSingleLine(true);

        field.setOnFocusChangeListener(
                (v, focused) -> {

                    if (focused) {
                        field.selectAll();
                    }
                }
        );

        field.addTextChangedListener(
                new TextWatcher() {

                    @Override
                    public void beforeTextChanged(
                            CharSequence s,
                            int start,
                            int count,
                            int after
                    ) {
                    }

                    @Override
                    public void onTextChanged(
                            CharSequence s,
                            int start,
                            int before,
                            int count
                    ) {

                        if (
                                suppressSearchWatcher ||
                                        voiceMode ||
                                        !isLoggedIn()
                        ) {
                            return;
                        }

                        String q = digitsOnly(
                                s == null ? "" : s.toString()
                        );

                        if (!q.equals(
                                s == null ? "" : s.toString()
                        )) {

                            suppressSearchWatcher = true;

                            field.setText(q);
                            field.setSelection(q.length());

                            suppressSearchWatcher = false;

                            return;
                        }

                        if (q.length() == 4) {

                            if (field == dashboardSearch) {

                                suppressSearchWatcher = true;

                                search.setText(q);
                                search.selectAll();

                                suppressSearchWatcher = false;

                                openSearch();

                            } else {

                                performSearch(q);
                            }

                        } else if (field == search) {

                            if (q.isEmpty()) {

                                clearResultsWithMessage(
                                        "Enter last 4 digits"
                                );

                            } else {

                                clearResultsWithMessage(
                                        "Enter " +
                                                (4 - q.length()) +
                                                " more digit(s)"
                                );
                            }
                        }
                    }

                    @Override
                    public void afterTextChanged(
                            Editable s
                    ) {
                    }
                }
        );
    }

    private boolean hasToken() {

        return prefs != null &&
                !prefs.getString(
                        "token",
                        ""
                ).trim().isEmpty();
    }

    private boolean isLoggedIn() {

        return hasToken() &&
                appPanel != null &&
                appPanel.getVisibility() == View.VISIBLE;
    }

    private String token() {

        return prefs
                .getString("token", "")
                .trim();
    }

    private String deviceId() {

        String id = Settings.Secure.getString(
                getContentResolver(),
                Settings.Secure.ANDROID_ID
        );

        return id == null ||
                id.trim().isEmpty()
                ? "android-unknown"
                : id;
    }

    private void showLogin() {

        loginPanel.setVisibility(View.VISIBLE);
        appPanel.setVisibility(View.GONE);
    }

    private void showApp() {

        loginPanel.setVisibility(View.GONE);
        appPanel.setVisibility(View.VISIBLE);

        try {

            JSONObject u =
                    new JSONObject(
                            prefs.getString(
                                    "user",
                                    "{}"
                            )
                    );

            String name =
                    u.optString(
                            "full_name",
                            ""
                    ).trim();

            if (name.isEmpty()) {

                name = u.optString(
                        "username",
                        "User"
                );
            }

            welcome.setText(
                    "Welcome, " + name
            );

            String role =
                    u.optString(
                            "role",
                            "REPO_AGENT"
                    );

            drawerRole.setText(role);

            applyRole(role);

        } catch (Exception ignored) {
        }

        updateModeButton();
        updateLastSyncText();
        showHome();

        try {
            JSONObject u = new JSONObject(prefs.getString("user", "{}"));
            if (online && "REPO_AGENT".equalsIgnoreCase(u.optString("role", ""))) {
                syncAll();
            }
        } catch (Exception ignored) {
        }

        handler.removeCallbacks(notificationPoll);
        handler.post(notificationPoll);
    }

    private void applyRole(String role) {

        boolean staff =
                "ADMIN".equalsIgnoreCase(role) ||
                        "OFFICE_STAFF".equalsIgnoreCase(role);

        int[] adminIds = {
                R.id.navUsers,
                R.id.navFiles,
                R.id.navBanks,
                R.id.navClients,
                R.id.navReports
        };

        for (int id : adminIds) {

            View v = findViewById(id);

            if (v != null) {

                v.setVisibility(
                        staff
                                ? View.VISIBLE
                                : View.GONE
                );
            }
        }
    }

    private void showPage(
            boolean dashboard,
            boolean searchPg,
            boolean module
    ) {

        dashboardPage.setVisibility(
                dashboard ? View.VISIBLE : View.GONE
        );
        searchPage.setVisibility(
                searchPg ? View.VISIBLE : View.GONE
        );
        modulePage.setVisibility(
                module ? View.VISIBLE : View.GONE
        );
    }

    private void rememberCurrentPage() {
        if ("home".equals(currentPageKey) ||
                "search".equals(currentPageKey) ||
                "profile".equals(currentPageKey) ||
                "notifications".equals(currentPageKey) ||
                "module".equals(currentPageKey)) {
            PageState last = new PageState(
                    currentPageKey,
                    currentModuleId,
                    currentModuleTitle
            );
            if (pageHistory.isEmpty() ||
                    !pageHistory.get(pageHistory.size() - 1).key.equals(last.key) ||
                    pageHistory.get(pageHistory.size() - 1).moduleId != last.moduleId) {
                pageHistory.add(last);
            }
        }
    }

    private void restorePage(PageState state) {
        if (state == null) {
            showHome();
            return;
        }

        if ("home".equals(state.key)) {
            currentPageKey = "home";
            currentModuleId = 0;
            currentModuleTitle = "";
            ((TextView) findViewById(R.id.pageTitle)).setText("Home");
            showPage(true, false, false);
            updateStats();
        } else if ("search".equals(state.key)) {
            currentPageKey = "search";
            currentModuleId = 0;
            currentModuleTitle = "";
            ((TextView) findViewById(R.id.pageTitle)).setText("Registration Search");
            showPage(false, true, false);
        } else if ("profile".equals(state.key)) {
            currentPageKey = "profile";
            showProfile();
        } else if ("notifications".equals(state.key)) {
            currentPageKey = "notifications";
            loadNotifications();
        } else if ("module".equals(state.key)) {
            currentPageKey = "module";
            currentModuleId = state.moduleId;
            currentModuleTitle = state.title;
            ((TextView) findViewById(R.id.pageTitle)).setText(state.title);
            showPage(false, false, true);
            loadModule(state.moduleId, state.title);
        }
    }

    private void showHome() {

        if (!isLoggedIn()) {

            showLogin();
            return;
        }

        pageHistory.clear();
        currentPageKey = "home";
        currentModuleId = 0;
        currentModuleTitle = "";

        ((TextView) findViewById(R.id.pageTitle))
                .setText("Home");

        showPage(
                true,
                false,
                false
        );

        updateStats();
    }

    private void openSearch() {

        if (!isLoggedIn()) {

            showLogin();
            return;
        }

        if (!restoringPage && !"search".equals(currentPageKey)) {
            rememberCurrentPage();
        }
        currentPageKey = "search";
        currentModuleId = 0;
        currentModuleTitle = "";

        ((TextView) findViewById(R.id.pageTitle))
                .setText("Registration Search");

        showPage(
                false,
                true,
                false
        );

        search.requestFocus();
        search.selectAll();

        InputMethodManager imm =
                (InputMethodManager)
                        getSystemService(
                                INPUT_METHOD_SERVICE
                        );

        if (imm != null) {

            imm.showSoftInput(
                    search,
                    InputMethodManager.SHOW_IMPLICIT
            );
        }
    }

    private void navigate(
            int id,
            String title
    ) {

        DrawerLayout drawer =
                findViewById(R.id.drawer);

        if (drawer != null) {

            drawer.closeDrawer(
                    Gravity.LEFT
            );
        }

        if (id == R.id.navDashboard) {

            showHome();

        } else if (id == R.id.navSearch) {

            openSearch();

        } else if (id == R.id.navNotifications) {

            if (!restoringPage && !"notifications".equals(currentPageKey)) rememberCurrentPage();
            currentPageKey = "notifications";
            currentModuleId = 0;
            currentModuleTitle = "";
            loadNotifications();

        } else {

            if (
                    (
                            id == R.id.navUsers ||
                                    id == R.id.navFiles ||
                                    id == R.id.navBanks ||
                                    id == R.id.navClients ||
                                    id == R.id.navReports ||
                                    id == R.id.navNotifications
                    ) &&
                            !isStaff()
            ) {

                moduleMessage.setText(
                        "This section is available only to admin/office staff."
                );

                showPage(
                        false,
                        false,
                        true
                );

                return;
            }

            ((TextView) findViewById(R.id.pageTitle))
                    .setText(title);

            if (!restoringPage && (!"module".equals(currentPageKey) ||
                    currentModuleId != id)) {
                rememberCurrentPage();
            }
            currentPageKey = "module";
            currentModuleId = id;
            currentModuleTitle = title;

            showPage(
                    false,
                    false,
                    true
            );

            loadModule(
                    id,
                    title
            );
        }
    }

    private boolean isStaff() {

        try {

            JSONObject u =
                    new JSONObject(
                            prefs.getString(
                                    "user",
                                    "{}"
                            )
                    );

            String role =
                    u.optString(
                            "role",
                            ""
                    );

            return
                    "ADMIN".equalsIgnoreCase(role) ||
                            "OFFICE_STAFF".equalsIgnoreCase(role);

        } catch (Exception e) {

            return false;
        }
    }

    private void loadModule(
            int navId,
            String title
    ) {

        String endpoint = null;

        if (navId == R.id.navUsers) {

            endpoint = "/users";

        } else if (
                navId == R.id.navFiles ||
                        navId == R.id.navTwo ||
                        navId == R.id.navFour ||
                        navId == R.id.navCv
        ) {

            endpoint = "/files";

        } else if (navId == R.id.navBanks) {

            endpoint = "/banks";

        } else if (navId == R.id.navClients) {

            endpoint = "/clients";

        } else if (navId == R.id.navReports) {

            endpoint = "/reports";

        } else if (navId == R.id.navNotifications) {

            endpoint = "/notifications";
        }

        if (endpoint == null) {

            moduleMessage.setText(
                    title + " is not available."
            );

            return;
        }

        if (!online) {

            moduleMessage.setText(
                    title + " requires online mode."
            );

            return;
        }

        final int moduleId = navId;

        moduleMessage.setText(
                "Loading " + title + "…"
        );

        authenticatedGet(
                endpoint,
                new Callback() {

                    @Override
                    public void onFailure(
                            Call call,
                            IOException e
                    ) {

                        runOnUiThread(
                                () ->
                                        moduleMessage.setText(
                                                "Unable to load " +
                                                        title +
                                                        ". Check connection."
                                        )
                        );
                    }

                    @Override
                    public void onResponse(
                            Call call,
                            Response response
                    ) throws IOException {

                        try (Response ignored = response) {

                            if (response.code() == 401) {

                                handleAuthExpired();
                                return;
                            }

                            String body =
                                    response.body() == null
                                            ? "[]"
                                            : response.body().string();

                            if (!response.isSuccessful()) {

                                JSONObject err;

                                try {

                                    err = parseObject(body);

                                } catch (JSONException e) {

                                    err = new JSONObject();
                                }

                                String msg =
                                        err.optString(
                                                "error",
                                                "Request failed (" +
                                                        response.code() +
                                                        ")"
                                        );

                                runOnUiThread(
                                        () ->
                                                moduleMessage.setText(msg)
                                );

                                return;
                            }

                            JSONArray arr =
                                    new JSONArray(body);

                            runOnUiThread(
                                    () ->
                                            renderModuleData(
                                                    moduleId,
                                                    title,
                                                    arr
                                            )
                            );

                        } catch (Exception e) {

                            runOnUiThread(
                                    () ->
                                            moduleMessage.setText(
                                                    "Unable to read server data."
                                            )
                            );
                        }
                    }
                }
        );
    }

    private void styleActionButton(Button button, int color) {
        button.setTextSize(15);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setAllCaps(false);
        button.setMinHeight(0);
        button.setMinWidth(0);
        button.setGravity(Gravity.CENTER);
        button.setPadding(12, 8, 12, 8);
        button.setTextColor(Color.WHITE);
        button.setBackgroundTintList(
                android.content.res.ColorStateList.valueOf(color)
        );
    }

    private LinearLayout.LayoutParams actionButtonParams(boolean first) {
        LinearLayout.LayoutParams lp =
                new LinearLayout.LayoutParams(0, 76, 1f);
        if (!first) lp.leftMargin = 8;
        return lp;
    }

    private void renderModuleData(
            int moduleId,
            String title,
            JSONArray arr
    ) {

        moduleMessage.setText("");

        LinearLayout box =
                new LinearLayout(this);

        box.setOrientation(
                LinearLayout.VERTICAL
        );

        if (moduleId == R.id.navFiles) {

            Button upload =
                    new Button(this);

            upload.setText(
                    "UPLOAD FILE"
            );

            upload.setOnClickListener(
                    v -> pickFileForUpload()
            );

            box.addView(
                    upload,
                    new LinearLayout.LayoutParams(
                            -1,
                            52
                    )
            );
        }

        int shown = 0;

        for (
                int i = 0;
                i < arr.length() && shown < 500;
                i++
        ) {

            JSONObject o =
                    arr.optJSONObject(i);

            if (o == null) {
                continue;
            }

            if (
                    (
                            moduleId == R.id.navTwo ||
                                    moduleId == R.id.navFour ||
                                    moduleId == R.id.navCv
                    ) &&
                            !fileMatchesNav(
                                    o,
                                    moduleId
                            )
            ) {
                continue;
            }

            LinearLayout row =
                    new LinearLayout(this);

            row.setOrientation(
                    LinearLayout.VERTICAL
            );

            row.setPadding(
                    12,
                    12,
                    12,
                    12
            );

            GradientDrawable bg =
                    new GradientDrawable();

            bg.setColor(Color.WHITE);

            bg.setCornerRadius(12);

            bg.setStroke(
                    1,
                    Color.rgb(
                            220,
                            226,
                            236
                    )
            );

            row.setBackground(bg);

            String heading;

            if (moduleId == R.id.navUsers) {

                heading =
                        o.optString(
                                "username",
                                "User"
                        ) +
                                " • " +
                                o.optString(
                                        "role",
                                        ""
                                );

            } else if (
                    moduleId == R.id.navFiles ||
                            moduleId == R.id.navTwo ||
                            moduleId == R.id.navFour ||
                            moduleId == R.id.navCv
            ) {

                heading =
                        o.optString(
                                "filename",
                                "File"
                        ) +
                                " • " +
                                o.optString(
                                        "record_count",
                                        "0"
                                ) +
                                " records";

            } else if (moduleId == R.id.navBanks) {

                heading =
                        o.optString(
                                "name",
                                "Bank"
                        ) +
                                " • " +
                                o.optString(
                                        "code",
                                        ""
                                );

            } else if (moduleId == R.id.navClients) {

                heading =
                        o.optString(
                                "name",
                                "Client"
                        );

            } else if (moduleId == R.id.navReports) {

                heading =
                        o.optString(
                                "name",
                                "Report"
                        ) +
                                " • " +
                                o.optString(
                                        "status",
                                        ""
                                );

            } else {

                heading =
                        o.optString(
                                "title",
                                "Notification"
                        );
            }

            row.addView(
                    text(
                            heading,
                            16,
                            Color.BLACK,
                            true
                    )
            );

            String detail =
                    o.optString(
                            "full_name",
                            o.optString(
                                    "message",
                                    o.optString(
                                            "code",
                                            o.optString(
                                                    "email",
                                                    ""
                                            )
                                    )
                            )
                    );

            if (!detail.isEmpty()) {

                row.addView(
                        text(
                                detail,
                                13,
                                Color.DKGRAY,
                                false
                        )
                );
            }

            if (
                    (
                            moduleId == R.id.navUsers ||
                                    moduleId == R.id.navFiles ||
                                    moduleId == R.id.navBanks ||
                                    moduleId == R.id.navClients ||
                                    moduleId == R.id.navReports
                    ) &&
                            isStaff()
            ) {

                LinearLayout actions =
                        new LinearLayout(this);

                actions.setOrientation(
                        LinearLayout.HORIZONTAL
                );

                int oid =
                        o.optInt(
                                "id",
                                0
                        );

                if (
                        moduleId == R.id.navUsers &&
                                oid > 0
                ) {

                    Button device =
                            new Button(this);

                    device.setText("Device Access");
                    styleActionButton(device, Color.rgb(23,66,122));

                    device.setOnClickListener(
                            v ->
                                    removeUserDeviceNative(
                                            oid
                                    )
                    );

                    actions.addView(
                            device,
                            actionButtonParams(true)
                    );

                    Button status =
                            new Button(this);

                    status.setText(
                            o.optBoolean("active", true)
                                    ? "Pause User"
                                    : "Activate User"
                    );
                    styleActionButton(status, Color.rgb(245,158,11));

                    status.setOnClickListener(
                            v ->
                                    toggleUserNative(
                                            oid,
                                            !o.optBoolean(
                                                    "active",
                                                    true
                                            )
                                    )
                    );

                    LinearLayout.LayoutParams slp = actionButtonParams(false);

                    actions.addView(
                            status,
                            slp
                    );

                    Button del =
                            new Button(this);

                    del.setText("Delete User");
                    styleActionButton(del, Color.rgb(180,35,63));

                    del.setOnClickListener(
                            v ->
                                    deleteUserNative(
                                            oid
                                    )
                    );

                    LinearLayout.LayoutParams dlp = actionButtonParams(false);

                    actions.addView(
                            del,
                            dlp
                    );

                } else if (
                        moduleId == R.id.navBanks &&
                                oid > 0
                ) {

                    Button edit =
                            new Button(this);

                    edit.setText("Edit Bank");
                    styleActionButton(edit, Color.rgb(23,66,122));

                    edit.setOnClickListener(
                            v ->
                                    editBankNative(o)
                    );

                    actions.addView(
                            edit,
                            actionButtonParams(true)
                    );

                    Button del =
                            new Button(this);

                    del.setText("Delete Bank");
                    styleActionButton(del, Color.rgb(180,35,63));

                    del.setOnClickListener(
                            v ->
                                    deleteBankNative(
                                            oid
                                    )
                    );

                    LinearLayout.LayoutParams blp = actionButtonParams(false);

                    actions.addView(
                            del,
                            blp
                    );

                } else if (
                        moduleId == R.id.navFiles &&
                                oid > 0
                ) {

                    Button del =
                            new Button(this);

                    del.setText("Delete File");
                    styleActionButton(del, Color.rgb(180,35,63));

                    del.setOnClickListener(
                            v ->
                                    deleteFileNative(
                                            oid
                                    )
                    );

                    actions.addView(
                            del,
                            new LinearLayout.LayoutParams(
                                    -1,
                                    76
                            )
                    );
                }

                box.addView(actions);
            }

            LinearLayout.LayoutParams rp =
                    new LinearLayout.LayoutParams(
                            -1,
                            LinearLayout.LayoutParams.WRAP_CONTENT
                    );

            rp.bottomMargin = 8;

            box.addView(
                    row,
                    rp
            );

            shown++;
        }

        if (shown == 0) {

            box.addView(
                    text(
                            "No records available.",
                            15,
                            Color.DKGRAY,
                            false
                    )
            );
        }

        /*
         * modulePage is now ViewGroup, so these operations compile.
         */
        if (modulePage.getChildCount() > 1) {

            modulePage.removeViews(
                    1,
                    modulePage.getChildCount() - 1
            );
        }

        modulePage.addView(box);
    }

    private boolean fileMatchesNav(
            JSONObject o,
            int navId
    ) {

        String t =
                o.optString(
                        "vehicle_type",
                        ""
                ).toUpperCase(Locale.ROOT);

        if (navId == R.id.navTwo) {

            return t.contains("TWO");
        }

        if (navId == R.id.navFour) {

            return t.contains("FOUR");
        }

        if (navId == R.id.navCv) {

            return t.contains("CV") ||
                    t.contains("COMMERCIAL");
        }

        return true;
    }

    private void removeUserDeviceNative(int id) {

        patchUserNative(
                id,
                new JSONObject(),
                "Device access removed."
        );
    }

    private void toggleUserNative(
            int id,
            boolean active
    ) {

        try {

            JSONObject b =
                    new JSONObject();

            b.put(
                    "active",
                    active
            );

            patchUserNative(
                    id,
                    b,
                    active
                            ? "User activated."
                            : "User paused."
            );

        } catch (Exception ignored) {
        }
    }

    private void deleteUserNative(int id) {

        if (!isStaff()) {
            return;
        }

        new AlertDialog.Builder(this)
                .setTitle("Delete User")
                .setMessage("Delete this user?")
                .setNegativeButton(
                        "CANCEL",
                        null
                )
                .setPositiveButton(
                        "DELETE",
                        (d, w) ->
                                authenticatedRequest(
                                        "/users/" + id,
                                        "DELETE",
                                        null,
                                        () ->
                                                loadModule(
                                                        R.id.navUsers,
                                                        "Users"
                                                )
                                )
                )
                .show();
    }

    private void patchUserNative(
            int id,
            JSONObject body,
            String success
    ) {

        if (!isStaff()) {
            return;
        }

        authenticatedRequest(
                "/users/" + id,
                "PATCH",
                body,
                () -> {

                    Toast.makeText(
                            this,
                            success,
                            Toast.LENGTH_SHORT
                    ).show();

                    loadModule(
                            R.id.navUsers,
                            "Users"
                    );
                }
        );
    }

    private void editBankNative(
            JSONObject bank
    ) {

        if (!isStaff()) {
            return;
        }

        LinearLayout form =
                new LinearLayout(this);

        form.setOrientation(
                LinearLayout.VERTICAL
        );

        form.setPadding(
                8,
                4,
                8,
                4
        );

        EditText name =
                new EditText(this);

        name.setHint(
                "Bank name"
        );

        name.setText(
                bank.optString(
                        "name",
                        ""
                )
        );

        form.addView(name);

        EditText code =
                new EditText(this);

        code.setHint(
                "Bank code"
        );

        code.setText(
                bank.optString(
                        "code",
                        ""
                )
        );

        form.addView(code);

        EditText phone =
                new EditText(this);

        phone.setHint(
                "Phone"
        );

        phone.setText(
                bank.optString(
                        "phone",
                        ""
                )
        );

        form.addView(phone);

        EditText email =
                new EditText(this);

        email.setHint(
                "Email"
        );

        email.setText(
                bank.optString(
                        "email",
                        ""
                )
        );

        form.addView(email);

        new AlertDialog.Builder(this)
                .setTitle("Update Bank")
                .setView(form)
                .setNegativeButton(
                        "CANCEL",
                        null
                )
                .setPositiveButton(
                        "SAVE",
                        (d, w) -> {

                            try {

                                JSONObject b =
                                        new JSONObject();

                                b.put(
                                        "name",
                                        name.getText()
                                                .toString()
                                                .trim()
                                );

                                b.put(
                                        "code",
                                        code.getText()
                                                .toString()
                                                .trim()
                                );

                                b.put(
                                        "phone",
                                        phone.getText()
                                                .toString()
                                                .trim()
                                );

                                b.put(
                                        "email",
                                        email.getText()
                                                .toString()
                                                .trim()
                                );

                                authenticatedRequest(
                                        "/banks/" +
                                                bank.optInt("id"),
                                        "PUT",
                                        b,
                                        () ->
                                                loadModule(
                                                        R.id.navBanks,
                                                        "Bank Management"
                                                )
                                );

                            } catch (Exception ignored) {
                            }
                        }
                )
                .show();
    }

    private void deleteBankNative(int id) {

        new AlertDialog.Builder(this)
                .setTitle("Delete Bank")
                .setMessage("Delete this bank?")
                .setNegativeButton(
                        "CANCEL",
                        null
                )
                .setPositiveButton(
                        "DELETE",
                        (d, w) ->
                                authenticatedRequest(
                                        "/banks/" + id,
                                        "DELETE",
                                        null,
                                        () ->
                                                loadModule(
                                                        R.id.navBanks,
                                                        "Bank Management"
                                                )
                                )
                )
                .show();
    }

    private void deleteFileNative(int id) {

        new AlertDialog.Builder(this)
                .setTitle("Delete File")
                .setMessage(
                        "Delete this file and imported records?"
                )
                .setNegativeButton(
                        "CANCEL",
                        null
                )
                .setPositiveButton(
                        "DELETE",
                        (d, w) ->
                                authenticatedRequest(
                                        "/files/" + id,
                                        "DELETE",
                                        null,
                                        () ->
                                                loadModule(
                                                        R.id.navFiles,
                                                        "File Management"
                                                )
                                )
                )
                .show();
    }

    private void authenticatedRequest(
            String path,
            String method,
            JSONObject body,
            Runnable success
    ) {

        Request.Builder b =
                new Request.Builder()
                        .url(
                                SERVER_URL +
                                        "/api" +
                                        path
                        )
                        .header(
                                "Authorization",
                                "Bearer " + token()
                        )
                        .header(
                                "Accept",
                                "application/json"
                        );

        RequestBody rb =
                body == null
                        ? null
                        : RequestBody.create(
                        body.toString(),
                        JSON
                );

        if ("DELETE".equals(method)) {

            b.delete();

        } else if ("PATCH".equals(method)) {

            b.patch(
                    rb == null
                            ? RequestBody.create(
                            new byte[0],
                            JSON
                    )
                            : rb
            );

        } else if ("POST".equals(method)) {

            b.post(
                    rb == null
                            ? RequestBody.create(
                            new byte[0],
                            JSON
                    )
                            : rb
            );

        } else if ("PUT".equals(method)) {

            b.put(
                    rb == null
                            ? RequestBody.create(
                            new byte[0],
                            JSON
                    )
                            : rb
            );

        } else {

            b.get();
        }

        client.newCall(
                b.build()
        ).enqueue(
                new Callback() {

                    @Override
                    public void onFailure(
                            Call c,
                            IOException e
                    ) {

                        runOnUiThread(
                                () ->
                                        Toast.makeText(
                                                MainActivity.this,
                                                "Request failed.",
                                                Toast.LENGTH_SHORT
                                        ).show()
                        );
                    }

                    @Override
                    public void onResponse(
                            Call c,
                            Response r
                    ) throws IOException {

                        try (Response ignored = r) {

                            if (r.code() == 401) {

                                handleAuthExpired();
                                return;
                            }

                            if (r.isSuccessful()) {

                                runOnUiThread(
                                        success
                                );

                            } else {

                                runOnUiThread(
                                        () ->
                                                Toast.makeText(
                                                        MainActivity.this,
                                                        "Request failed (" +
                                                                r.code() +
                                                                ").",
                                                        Toast.LENGTH_SHORT
                                                ).show()
                                );
                            }
                        }
                    }
                }
        );
    }

    /*
     * Upload code intentionally left as-is.
     */
    private void pickFileForUpload() {

        if (!isStaff()) {
            return;
        }

        Intent i =
                new Intent(
                        Intent.ACTION_OPEN_DOCUMENT
                );

        i.addCategory(
                Intent.CATEGORY_OPENABLE
        );

        i.setType("*/*");

        i.putExtra(
                Intent.EXTRA_MIME_TYPES,
                new String[]{
                        "text/csv",
                        "application/vnd.ms-excel",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                }
        );

        startActivityForResult(
                i,
                FILE_PICK_REQ
        );
    }

    @Override
    protected void onActivityResult(
            int requestCode,
            int resultCode,
            Intent data
    ) {

        super.onActivityResult(
                requestCode,
                resultCode,
                data
        );

        if (
                requestCode != FILE_PICK_REQ ||
                        resultCode != RESULT_OK ||
                        data == null ||
                        data.getData() == null
        ) {
            return;
        }

        Uri uri =
                data.getData();

        final long size =
                getUriSize(uri);

        if (size > 50L * 1024L * 1024L) {

            Toast.makeText(
                    this,
                    "File size cannot exceed 50 MB.",
                    Toast.LENGTH_LONG
            ).show();

            return;
        }

        authenticatedGet(
                "/banks",
                new Callback() {

                    @Override
                    public void onFailure(
                            Call c,
                            IOException e
                    ) {

                        runOnUiThread(
                                () ->
                                        Toast.makeText(
                                                MainActivity.this,
                                                "Unable to load banks.",
                                                Toast.LENGTH_SHORT
                                        ).show()
                        );
                    }

                    @Override
                    public void onResponse(
                            Call c,
                            Response r
                    ) throws IOException {

                        try (Response ignored = r) {

                            if (r.code() == 401) {

                                handleAuthExpired();
                                return;
                            }

                            String body =
                                    r.body() == null
                                            ? "[]"
                                            : r.body().string();

                            if (!r.isSuccessful()) {
                                return;
                            }

                            try {

                                JSONArray banks =
                                        new JSONArray(body);

                                runOnUiThread(
                                        () ->
                                                chooseBankAndUpload(
                                                        uri,
                                                        banks
                                                )
                                );

                            } catch (Exception ignored2) {
                            }
                        }
                    }
                }
        );
    }

    private long getUriSize(Uri uri) {

        try (
                Cursor c =
                        getContentResolver().query(
                                uri,
                                null,
                                null,
                                null,
                                null
                        )
        ) {

            if (
                    c != null &&
                            c.moveToFirst()
            ) {

                int i =
                        c.getColumnIndex(
                                OpenableColumns.SIZE
                        );

                if (
                        i >= 0 &&
                                !c.isNull(i)
                ) {

                    return c.getLong(i);
                }
            }

        } catch (Exception ignored) {
        }

        return 0;
    }

    private String getUriName(Uri uri) {

        try (
                Cursor c =
                        getContentResolver().query(
                                uri,
                                null,
                                null,
                                null,
                                null
                        )
        ) {

            if (
                    c != null &&
                            c.moveToFirst()
            ) {

                int i =
                        c.getColumnIndex(
                                OpenableColumns.DISPLAY_NAME
                        );

                if (i >= 0) {

                    return c.getString(i);
                }
            }

        } catch (Exception ignored) {
        }

        return "upload.xlsx";
    }

    private void chooseBankAndUpload(
            Uri uri,
            JSONArray banks
    ) {

        String[] names =
                new String[banks.length()];

        for (
                int i = 0;
                i < banks.length();
                i++
        ) {

            JSONObject bank =
                    banks.optJSONObject(i);

            names[i] =
                    bank == null
                            ? "Bank"
                            : bank.optString(
                            "name",
                            "Bank"
                    );
        }

        new AlertDialog.Builder(this)
                .setTitle("Select Bank")
                .setItems(
                        names,
                        (d, which) -> {

                            JSONObject bank =
                                    banks.optJSONObject(
                                            which
                                    );

                            int bankId =
                                    bank == null
                                            ? 0
                                            : bank.optInt(
                                            "id",
                                            0
                                    );

                            uploadFileNative(
                                    uri,
                                    bankId
                            );
                        }
                )
                .setNegativeButton(
                        "CANCEL",
                        null
                )
                .show();
    }

    private void uploadFileNative(
            Uri uri,
            int bankId
    ) {

        try {

            java.io.InputStream in =
                    getContentResolver()
                            .openInputStream(uri);

            if (in == null) {

                throw new IOException(
                        "Cannot open file"
                );
            }

            java.io.ByteArrayOutputStream out =
                    new java.io.ByteArrayOutputStream();

            byte[] buf =
                    new byte[8192];

            int n;
            long total = 0;

            while (
                    (n = in.read(buf)) != -1
            ) {

                total += n;

                if (
                        total >
                                50L *
                                        1024L *
                                        1024L
                ) {

                    in.close();

                    throw new IOException(
                            "File exceeds 50 MB"
                    );
                }

                out.write(
                        buf,
                        0,
                        n
                );
            }

            in.close();

            byte[] bytes =
                    out.toByteArray();

            String filename =
                    getUriName(uri);

            RequestBody fileBody =
                    RequestBody.create(
                            bytes,
                            MediaType.parse(
                                    "application/octet-stream"
                            )
                    );

            MultipartBody body =
                    new MultipartBody.Builder()
                            .setType(
                                    MultipartBody.FORM
                            )
                            .addFormDataPart(
                                    "file",
                                    filename,
                                    fileBody
                            )
                            .addFormDataPart(
                                    "bank_id",
                                    String.valueOf(
                                            bankId
                                    )
                            )
                            .build();

            Request req =
                    new Request.Builder()
                            .url(
                                    SERVER_URL +
                                            "/api/files/import"
                            )
                            .header(
                                    "Authorization",
                                    "Bearer " +
                                            token()
                            )
                            .post(body)
                            .build();

            Toast.makeText(
                    this,
                    "Uploading…",
                    Toast.LENGTH_SHORT
            ).show();

            client.newCall(req)
                    .enqueue(
                            new Callback() {

                                @Override
                                public void onFailure(
                                        Call c,
                                        IOException e
                                ) {

                                    runOnUiThread(
                                            () ->
                                                    Toast.makeText(
                                                            MainActivity.this,
                                                            "Upload failed: " +
                                                                    e.getMessage(),
                                                            Toast.LENGTH_LONG
                                                    ).show()
                                    );
                                }

                                @Override
                                public void onResponse(
                                        Call c,
                                        Response r
                                ) throws IOException {

                                    try (
                                            Response ignored =
                                                    r
                                    ) {

                                        String bodyText =
                                                r.body() == null
                                                        ? ""
                                                        : r.body().string();

                                        if (r.code() == 401) {

                                            handleAuthExpired();
                                            return;
                                        }

                                        try {

                                            JSONObject o =
                                                    parseObject(
                                                            bodyText
                                                    );

                                            runOnUiThread(
                                                    () ->
                                                            Toast.makeText(
                                                                    MainActivity.this,
                                                                    r.isSuccessful()
                                                                            ? "File uploaded successfully."
                                                                            : o.optString(
                                                                            "error",
                                                                            "Upload failed."
                                                                    ),
                                                                    Toast.LENGTH_LONG
                                                            ).show()
                                            );

                                        } catch (Exception ex) {

                                            runOnUiThread(
                                                    () ->
                                                            Toast.makeText(
                                                                    MainActivity.this,
                                                                    "Upload failed.",
                                                                    Toast.LENGTH_LONG
                                                            ).show()
                                            );
                                        }

                                        if (r.isSuccessful()) {

                                            runOnUiThread(
                                                    () ->
                                                            loadModule(
                                                                    R.id.navFiles,
                                                                    "File Management"
                                                            )
                                            );
                                        }
                                    }
                                }
                            }
                    );

        } catch (Exception e) {

            Toast.makeText(
                    this,
                    e.getMessage(),
                    Toast.LENGTH_LONG
            ).show();
        }
    }

    private void updateLastSyncText() {
        if (lastSyncText == null || prefs == null) return;
        long last = prefs.getLong("last_sync_at", 0L);
        if (last <= 0) {
            lastSyncText.setText("Not synced yet");
            return;
        }
        String formatted = new java.text.SimpleDateFormat(
                "dd MMM yyyy - hh:mm a",
                Locale.getDefault()
        ).format(new java.util.Date(last));
        lastSyncText.setText(formatted);
    }

    private void updateModeButton() {
        Button b = findViewById(R.id.modeBtn);
        if (b == null) return;
        b.setText(online ? "ONLINE" : "OFFLINE");
        b.setBackgroundTintList(
                android.content.res.ColorStateList.valueOf(
                        Color.parseColor(online ? "#159447" : "#64748B")
                )
        );
    }

    private void toggleMode() {

        online = !online;
        prefs.edit().putBoolean("online_mode", online).apply();

        Button b =
                findViewById(
                        R.id.modeBtn
                );

        b.setText(
                online
                        ? "ONLINE"
                        : "OFFLINE"
        );

        b.setBackgroundTintList(
                android.content.res.ColorStateList.valueOf(
                        Color.parseColor(
                                online
                                        ? "#159447"
                                        : "#64748B"
                        )
                )
        );

        if (online) {

            updateStats();
            pollNotifications();

        } else {

            activityText.setText(
                    "Offline mode • local database active"
            );

            syncStatus.setText(
                    "Offline mode • searches use downloaded records"
            );
        }
    }

    private void login() {

        String u =
                username.getText()
                        .toString()
                        .trim();

        String p =
                password.getText()
                        .toString();

        if (u.isEmpty()) {

            loginResult.setText(
                    "Username, email or mobile is required."
            );

            return;
        }

        if (p.isEmpty()) {

            loginResult.setText(
                    "Password is required."
            );

            return;
        }

        loginResult.setText(
                "Connecting to server…"
        );

        try {

            JSONObject body =
                    new JSONObject();

            body.put(
                    "username",
                    u
            );

            body.put(
                    "password",
                    p
            );

            body.put(
                    "device_id",
                    deviceId()
            );

            Request request =
                    new Request.Builder()
                            .url(
                                    SERVER_URL +
                                            "/api/auth/login"
                            )
                            .post(
                                    RequestBody.create(
                                            body.toString(),
                                            JSON
                                    )
                            )
                            .header(
                                    "Accept",
                                    "application/json"
                            )
                            .build();

            client.newCall(request)
                    .enqueue(
                            new Callback() {

                                @Override
                                public void onFailure(
                                        Call call,
                                        IOException e
                                ) {

                                    runOnUiThread(
                                            () ->
                                                    loginResult.setText(
                                                            "Server connection failed. Check internet connection."
                                                    )
                                    );
                                }

                                @Override
                                public void onResponse(
                                        Call call,
                                        Response response
                                ) throws IOException {

                                    try (
                                            Response ignored =
                                                    response
                                    ) {

                                        String text =
                                                response.body() == null
                                                        ? ""
                                                        : response.body().string();

                                        JSONObject json;

                                        try {

                                            json =
                                                    parseObject(
                                                            text
                                                    );

                                        } catch (
                                                JSONException e
                                        ) {

                                            runOnUiThread(
                                                    () ->
                                                            loginResult.setText(
                                                                    "Invalid server response."
                                                            )
                                            );

                                            return;
                                        }

                                        if (!response.isSuccessful()) {

                                            String msg =
                                                    json.optString(
                                                            "error",
                                                            "Login failed (" +
                                                                    response.code() +
                                                                    ")"
                                                    );

                                            runOnUiThread(
                                                    () ->
                                                            loginResult.setText(
                                                                    msg
                                                            )
                                            );

                                            return;
                                        }

                                        String t =
                                                json.optString(
                                                        "token",
                                                        json.optString(
                                                                "access_token",
                                                                ""
                                                        )
                                                ).trim();

                                        JSONObject user =
                                                json.optJSONObject(
                                                        "user"
                                                );

                                        if (t.isEmpty()) {

                                            runOnUiThread(
                                                    () ->
                                                            loginResult.setText(
                                                                    "Server returned no login token."
                                                            )
                                            );

                                            return;
                                        }

                                        prefs.edit()
                                                .putString(
                                                        "token",
                                                        t
                                                )
                                                .putString(
                                                        "user",
                                                        user == null
                                                                ? "{}"
                                                                : user.toString()
                                                )
                                                .apply();

                                        runOnUiThread(
                                                () -> {

                                                    loginResult.setText(
                                                            ""
                                                    );

                                                    showApp();
                                                }
                                        );
                                    }
                                }
                            }
                    );

        } catch (Exception e) {

            loginResult.setText(
                    "Login request failed."
            );
        }
    }

    private void updateStats() {

        int local =
                localDb.count();

        stats.setText(
                local + " records"
        );

        if (
                !isLoggedIn() ||
                        !online
        ) {
            return;
        }

        /*
         * authenticatedGet already receives the complete path
         * relative to /api, so use /dashboard here.
         */
        authenticatedGet(
                "/dashboard",
                new Callback() {

                    @Override
                    public void onFailure(
                            Call call,
                            IOException e
                    ) {
                    }

                    @Override
                    public void onResponse(
                            Call call,
                            Response response
                    ) throws IOException {

                        try (
                                Response ignored =
                                        response
                        ) {

                            if (response.code() == 401) {

                                handleAuthExpired();
                                return;
                            }

                            String body =
                                    response.body() == null
                                            ? ""
                                            : response.body().string();

                            JSONObject o;

                            try {

                                o =
                                        parseObject(body);

                            } catch (JSONException e) {

                                return;
                            }

                            if (!response.isSuccessful()) {
                                return;
                            }

                            int total =
                                    o.optInt(
                                            "total",
                                            local
                                    );

                            runOnUiThread(
                                    () -> {

                                        totalStat.setText(
                                                String.valueOf(
                                                        total
                                                )
                                        );

                                        activityText.setText(
                                                "Server connected • " +
                                                        total +
                                                        " assigned records available"
                                        );
                                    }
                            );
                        }
                    }
                }
        );
    }

    private void performSearch(
            String q
    ) {

        if (!isLoggedIn()) {

            showLogin();
            return;
        }

        q =
                digitsOnly(q);

        if (q.length() != 4) {
            return;
        }

        JSONArray local =
                localDb.searchRegistrationSuffix(q);

        renderCards(
                local,
                "Registration matches for last 4 digits: " +
                        q
        );

        search.selectAll();

        if (!online) {
            return;
        }

        final String query = q;

        /*
         * Correct path: authenticatedGet adds /api.
         */
        authenticatedGet(
                "/search/registration-suffix?suffix=" +
                        Uri.encode(query),
                new Callback() {

                    @Override
                    public void onFailure(
                            Call call,
                            IOException e
                    ) {
                        // Local results remain visible.
                    }

                    @Override
                    public void onResponse(
                            Call call,
                            Response response
                    ) throws IOException {

                        try (
                                Response ignored =
                                        response
                        ) {

                            if (response.code() == 401) {

                                handleAuthExpired();
                                return;
                            }

                            String body =
                                    response.body() == null
                                            ? "[]"
                                            : response.body().string();

                            if (!response.isSuccessful()) {
                                return;
                            }

                            JSONArray remote =
                                    new JSONArray(body);

                            for (
                                    int i = 0;
                                    i < remote.length();
                                    i++
                            ) {

                                localDb.upsert(
                                        remote.getJSONObject(i)
                                );
                            }

                            runOnUiThread(
                                    () -> {

                                        renderCards(
                                                remote,
                                                "Registration matches for last 4 digits: " +
                                                        query
                                        );

                                        search.requestFocus();
                                        search.selectAll();
                                    }
                            );

                        } catch (Exception ignored) {
                        }
                    }
                }
        );
    }

    private void renderCards(
            JSONArray array,
            String heading
    ) {

        resultList.removeAllViews();

        int count =
                array == null
                        ? 0
                        : array.length();

        resultCount.setText(
                count +
                        " match" +
                        (count == 1 ? "" : "es")
        );

        resultMain.setText(
                count == 0
                        ? "No registration matches found."
                        : heading
        );

        if (array == null) {
            return;
        }

        for (
                int i = 0;
                i < array.length();
                i++
        ) {

            JSONObject v =
                    array.optJSONObject(i);

            if (v == null) {
                continue;
            }

            LinearLayout card =
                    new LinearLayout(this);

            card.setOrientation(
                    LinearLayout.VERTICAL
            );

            card.setGravity(
                    Gravity.CENTER_VERTICAL
            );

            card.setPadding(
                    10,
                    5,
                    10,
                    5
            );

            GradientDrawable bg =
                    new GradientDrawable();

            bg.setColor(
                    Color.WHITE
            );

            bg.setCornerRadius(
                    12
            );

            bg.setStroke(
                    1,
                    Color.rgb(
                            220,
                            226,
                            236
                    )
            );

            card.setBackground(bg);

            GridLayout.LayoutParams lp =
                    new GridLayout.LayoutParams();

            lp.width = 0;
            lp.height = 62;

            lp.columnSpec =
                    GridLayout.spec(
                            GridLayout.UNDEFINED,
                            1f
                    );

            lp.setMargins(
                    3,
                    3,
                    3,
                    3
            );

            card.setLayoutParams(lp);

            card.addView(
                    text(
                            safe(
                                    v,
                                    "registration_no"
                            ),
                            15,
                            Color.BLACK,
                            true
                    )
            );

            card.addView(
                    text(
                            "Registration No",
                            9,
                            Color.rgb(
                                    100,
                                    116,
                                    139
                            ),
                            false
                    )
            );

            card.setOnClickListener(
                    x ->
                            showDetails(v)
            );

            resultList.addView(card);
        }
    }

    private TextView text(
            String value,
            int size,
            int color,
            boolean bold
    ) {

        TextView t =
                new TextView(this);

        t.setText(
                value == null
                        ? ""
                        : value
        );

        t.setTextSize(size);
        t.setTextColor(color);
        t.setMaxLines(2);

        if (bold) {

            t.setTypeface(
                    Typeface.DEFAULT,
                    Typeface.BOLD
            );
        }

        return t;
    }

    private void showDetails(
            JSONObject vehicle
    ) {

        if (vehicle == null) {
            return;
        }

        LinearLayout box =
                new LinearLayout(this);

        box.setOrientation(
                LinearLayout.VERTICAL
        );

        box.setPadding(
                18,
                10,
                18,
                10
        );

        box.setBackgroundColor(
                Color.WHITE
        );

        box.addView(
                text(
                        safe(
                                vehicle,
                                "registration_no"
                        ),
                        24,
                        Color.BLACK,
                        true
                )
        );

        String[] fields = {
                "registration_no",
                "customer_name",
                "vehicle_type",
                "bank_name",
                "chassis_no",
                "engine_no",
                "mobile",
                "city",
                "state",
                "pincode",
                "status",
                "account_no",
                "loan_no"
        };

        for (String k : fields) {

            if (vehicle.has(k)) {

                box.addView(
                        text(
                                label(k) +
                                        ": " +
                                        safe(
                                                vehicle,
                                                k
                                        ),
                                14,
                                Color.BLACK,
                                false
                        )
                );
            }
        }

        Button share =
                new Button(this);

        share.setText(
                "CONFIRM & SHARE ON WHATSAPP"
        );
        share.setTextSize(16);
        share.setAllCaps(false);
        share.setMinHeight(0);
        share.setGravity(Gravity.CENTER);
        share.setPadding(18, 12, 18, 12);

        share.setTextColor(
                Color.WHITE
        );

        share.setBackgroundTintList(
                android.content.res.ColorStateList.valueOf(
                        Color.rgb(
                                37,
                                140,
                                70
                        )
                )
        );

        LinearLayout.LayoutParams shareLp =
                new LinearLayout.LayoutParams(
                        -1,
                        76
                );

        shareLp.topMargin = 14;

        box.addView(
                share,
                shareLp
        );

        ScrollView scroll =
                new ScrollView(this);

        scroll.setBackgroundColor(
                Color.WHITE
        );

        scroll.addView(box);

        AlertDialog dialog =
                new AlertDialog.Builder(this)
                        .setTitle(
                                "Vehicle Details"
                        )
                        .setView(scroll)
                        .setNegativeButton(
                                "CLOSE",
                                null
                        )
                        .create();

        dialog.setOnShowListener(
                d ->
                        share.setOnClickListener(
                                v ->
                                        shareWhatsApp(
                                                vehicle
                                        )
                        )
        );

        dialog.show();
        Button close = dialog.getButton(AlertDialog.BUTTON_NEGATIVE);
        if (close != null) {
            close.setTextSize(16);
            close.setMinHeight(64);
            close.setPadding(20, 10, 20, 10);
        }
    }

    private String safe(
            JSONObject o,
            String key
    ) {

        String s =
                o == null
                        ? ""
                        : o.optString(
                        key,
                        ""
                ).trim();

        return s.isEmpty() ||
                "null".equalsIgnoreCase(s)
                ? "N/A"
                : s;
    }

    private String label(
            String key
    ) {

        return key
                .replace(
                        '_',
                        ' '
                )
                .toUpperCase(
                        Locale.ROOT
                );
    }

    private void shareWhatsApp(
            JSONObject vehicle
    ) {

        StringBuilder out =
                new StringBuilder(
                        "RecoveryOps Vehicle Details\n\n"
                );

        out.append("BANK NAME: ")
                .append(safe(vehicle, "bank_name"))
                .append('\n');

        Iterator<String> it =
                vehicle.keys();

        while (it.hasNext()) {

            String key =
                    it.next();

            if (
                    "id".equals(key) ||
                            "version".equals(key) ||
                            "source_file_id".equals(key) ||
                            "bank_name".equals(key)
            ) {
                continue;
            }

            out.append(
                            label(key)
                    )
                    .append(": ")
                    .append(
                            safe(
                                    vehicle,
                                    key
                            )
                    )
                    .append('\n');
        }

        Intent intent =
                new Intent(
                        Intent.ACTION_SEND
                );

        intent.setType(
                "text/plain"
        );

        intent.putExtra(
                Intent.EXTRA_TEXT,
                out.toString()
        );

        try {

            intent.setPackage(
                    "com.whatsapp"
            );

            startActivity(intent);

        } catch (Exception e) {

            intent.setPackage(null);

            startActivity(
                    Intent.createChooser(
                            intent,
                            "Share vehicle details"
                    )
            );
        }
    }

    private void syncAll() {

        if (!isLoggedIn()) {
            showLogin();
            return;
        }

        if (!online) {
            syncStatus.setText("Offline mode");
            return;
        }

        if (syncing) return;

        syncing = true;
        syncTotal = 0;
        syncDownloaded = 0;
        syncStatus.setText("Syncing… 0%");

        Intent intent = new Intent(this, SyncService.class);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(intent);
            } else {
                startService(intent);
            }
        } catch (Exception e) {
            syncing = false;
            syncStatus.setText("Sync failed");
        }
    }

    private void downloadPage(
            int afterVersion,
            int afterId
    ) {

        authenticatedGet(
                "/sync/download?after_version=" +
                        afterVersion +
                        "&after_id=" +
                        afterId +
                        "&limit=5000",
                new Callback() {

                    @Override
                    public void onFailure(
                            Call call,
                            IOException e
                    ) {

                        runOnUiThread(
                                () -> {

                                    syncing = false;

                                    syncStatus.setText(
                                            "Sync failed. Check connection."
                                    );
                                }
                        );
                    }

                    @Override
                    public void onResponse(
                            Call call,
                            Response response
                    ) throws IOException {

                        try (
                                Response ignored =
                                        response
                        ) {

                            if (response.code() == 401) {

                                handleAuthExpired();
                                return;
                            }

                            String body =
                                    response.body() == null
                                            ? ""
                                            : response.body().string();

                            JSONObject o =
                                    parseObject(body);

                            if (!response.isSuccessful()) {

                                throw new Exception(
                                        o.optString(
                                                "error",
                                                "Sync failed"
                                        )
                                );
                            }

                            JSONArray rows =
                                    o.optJSONArray(
                                            "records"
                                    );

                            if (rows != null) {

                                localDb.upsertBatch(rows);
                            }

                            syncDownloaded =
                                    localDb.count();

                            int total =
                                    o.optInt(
                                            "total",
                                            syncTotal
                                    );

                            if (total > 0) {

                                syncTotal = total;
                            }

                            int nextVersion =
                                    o.optInt(
                                            "next_version",
                                            afterVersion
                                    );

                            int nextId =
                                    o.optInt(
                                            "next_id",
                                            afterId
                                    );

                            boolean more =
                                    o.optBoolean(
                                            "has_more",
                                            false
                                    );

                            int pct =
                                    syncTotal <= 0
                                            ? (
                                            more
                                                    ? 0
                                                    : 100
                                    )
                                            : Math.min(
                                            100,
                                            (int) Math.round(
                                                    (
                                                            syncDownloaded *
                                                                    100.0
                                                    ) /
                                                            syncTotal
                                            )
                                    );

                            runOnUiThread(
                                    () -> {

                                        stats.setText(
                                                syncDownloaded +
                                                        " records"
                                        );

                                        syncStatus.setText(
                                                "Downloading… " +
                                                        pct +
                                                        "%  •  " +
                                                        syncDownloaded +
                                                        "/" +
                                                        syncTotal
                                        );
                                    }
                            );

                            if (
                                    more &&
                                            (
                                                    nextVersion >
                                                            afterVersion ||
                                                            (
                                                                    nextVersion ==
                                                                            afterVersion &&
                                                                            nextId >
                                                                                    afterId
                                                            )
                                            )
                            ) {

                                downloadPage(
                                        nextVersion,
                                        nextId
                                );

                            } else {

                                runOnUiThread(
                                        () -> {

                                            syncing = false;

                                            int finalCount =
                                                    localDb.count();

                                            syncStatus.setText(
                                                    "Sync complete • 100% • " +
                                                            finalCount +
                                                            " records offline"
                                            );

                                            totalStat.setText(
                                                    String.valueOf(
                                                            syncTotal > 0
                                                                    ? syncTotal
                                                                    : finalCount
                                                    )
                                            );

                                            activityText.setText(
                                                    "Offline database updated • " +
                                                            finalCount +
                                                            " records"
                                            );
                                        }
                                );
                            }

                        } catch (Exception e) {

                            runOnUiThread(
                                    () -> {

                                        syncing = false;

                                        syncStatus.setText(
                                                "Sync failed: " +
                                                        e.getMessage()
                                        );
                                    }
                            );
                        }
                    }
                }
        );
    }

    private void loadNotifications() {

        if (!isLoggedIn()) {

            showLogin();
            return;
        }

        if (!"notifications".equals(currentPageKey)) rememberCurrentPage();
        currentPageKey = "notifications";
        currentModuleId = 0;
        currentModuleTitle = "";

        showPage(
                false,
                false,
                true
        );

        ((TextView) findViewById(R.id.pageTitle))
                .setText("Notifications");

        if (!online) {

            moduleMessage.setText(
                    "Notifications require online mode."
            );

            return;
        }

        authenticatedGet(
                "/notifications",
                new Callback() {

                    @Override
                    public void onFailure(
                            Call call,
                            IOException e
                    ) {

                        runOnUiThread(
                                () ->
                                        moduleMessage.setText(
                                                "Notifications unavailable."
                                        )
                        );
                    }

                    @Override
                    public void onResponse(
                            Call call,
                            Response response
                    ) throws IOException {

                        try (
                                Response ignored =
                                        response
                        ) {

                            if (response.code() == 401) {

                                handleAuthExpired();
                                return;
                            }

                            String body =
                                    response.body() == null
                                            ? "[]"
                                            : response.body().string();

                            JSONArray a =
                                    new JSONArray(body);

                            StringBuilder text =
                                    new StringBuilder();

                            for (
                                    int i = 0;
                                    i < a.length();
                                    i++
                            ) {

                                JSONObject n =
                                        a.getJSONObject(i);

                                text.append("• ")
                                        .append(
                                                n.optString(
                                                        "title"
                                                )
                                        )
                                        .append("\n  ")
                                        .append(
                                                n.optString(
                                                        "message"
                                                )
                                        )
                                        .append("\n\n");
                            }

                            runOnUiThread(
                                    () ->
                                            moduleMessage.setText(
                                                    text.length() == 0
                                                            ? "No notifications."
                                                            : text.toString()
                                            )
                            );

                        } catch (Exception ignored) {
                        }
                    }
                }
        );
    }

    private void showProfile() {

        if (!isLoggedIn()) {

            showLogin();
            return;
        }

        if (!restoringPage && !"profile".equals(currentPageKey)) rememberCurrentPage();
        currentPageKey = "profile";
        currentModuleId = 0;
        currentModuleTitle = "";

        showPage(
                false,
                false,
                true
        );

        ((TextView) findViewById(R.id.pageTitle))
                .setText("Profile");

        try {

            JSONObject u =
                    new JSONObject(
                            prefs.getString(
                                    "user",
                                    "{}"
                            )
                    );

            moduleMessage.setText(
                    "Profile\n\nName: " +
                            u.optString(
                                    "full_name",
                                    "User"
                            ) +
                            "\nUsername: " +
                            u.optString(
                                    "username",
                                    ""
                            ) +
                            "\nRole: " +
                            u.optString(
                                    "role",
                                    ""
                            ) +
                            "\nDevice: This device" +
                            "\nMode: " +
                            (
                                    online
                                            ? "Online"
                                            : "Offline"
                            )
            );

        } catch (Exception ignored) {
        }
    }

    private void logoutNow() {

        String oldToken =
                token();

        if (!oldToken.isEmpty()) {

            Request request =
                    new Request.Builder()
                            .url(
                                    SERVER_URL +
                                            "/api/auth/logout"
                            )
                            .header(
                                    "Authorization",
                                    "Bearer " +
                                            oldToken
                            )
                            .post(
                                    RequestBody.create(
                                            new byte[0],
                                            null
                                    )
                            )
                            .build();

            client.newCall(request)
                    .enqueue(
                            new Callback() {

                                @Override
                                public void onFailure(
                                        Call call,
                                        IOException e
                                ) {
                                }

                                @Override
                                public void onResponse(
                                        Call call,
                                        Response response
                                ) throws IOException {

                                    response.close();
                                }
                            }
                    );
        }

        try { stopService(new Intent(this, SyncService.class)); } catch (Exception ignored) {}

        prefs.edit()
                .clear()
                .apply();

        search.setText("");
        dashboardSearch.setText("");

        resultList.removeAllViews();

        handler.removeCallbacks(
                notificationPoll
        );

        showLogin();
    }

    private void authenticatedGet(
            String path,
            Callback callback
    ) {

        /*
         * All API endpoints in this file are passed without /api.
         */
        Request request =
                new Request.Builder()
                        .url(
                                SERVER_URL +
                                        "/api" +
                                        path
                        )
                        .header(
                                "Authorization",
                                "Bearer " +
                                        token()
                        )
                        .header(
                                "Accept",
                                "application/json"
                        )
                        .get()
                        .build();

        client.newCall(request)
                .enqueue(callback);
    }

    private void handleAuthExpired() {

        runOnUiThread(
                () -> {

                    prefs.edit()
                            .clear()
                            .apply();

                    syncing = false;

                    showLogin();

                    loginResult.setText(
                            "Session expired. Please login again."
                    );
                }
        );
    }

    private JSONObject parseObject(
            String body
    ) throws JSONException {

        String clean =
                body == null
                        ? ""
                        : body.trim();

        if (
                clean.isEmpty() ||
                        !clean.startsWith("{")
        ) {

            throw new JSONException(
                    "Invalid server response"
            );
        }

        return new JSONObject(clean);
    }

    private void clearResultsWithMessage(
            String message
    ) {

        resultCount.setText("0");

        resultMain.setText(
                message
        );

        resultList.removeAllViews();
    }

    private String digitsOnly(
            String s
    ) {

        return s == null
                ? ""
                : s.replaceAll(
                "[^0-9]",
                ""
        );
    }

    private void startVoice() {

        if (
                checkSelfPermission(
                        Manifest.permission.RECORD_AUDIO
                ) != PackageManager.PERMISSION_GRANTED
        ) {

            requestPermissions(
                    new String[]{
                            Manifest.permission.RECORD_AUDIO
                    },
                    AUDIO_REQ
            );

            return;
        }

        if (
                !SpeechRecognizer
                        .isRecognitionAvailable(this)
        ) {

            resultMain.setText(
                    "Voice recognition is not available. Type 4 digits."
            );

            return;
        }

        final SpeechRecognizer recognizer =
                SpeechRecognizer
                        .createSpeechRecognizer(
                                this
                        );

        Intent intent =
                new Intent(
                        RecognizerIntent.ACTION_RECOGNIZE_SPEECH
                );

        intent.putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
        );

        intent.putExtra(
                RecognizerIntent.EXTRA_LANGUAGE,
                "hi-IN"
        );

        intent.putExtra(
                RecognizerIntent.EXTRA_MAX_RESULTS,
                5
        );

        voiceMode = true;

        resultMain.setText(
                "Listening… say four digits."
        );

        recognizer.setRecognitionListener(
                new RecognitionListener() {

                    @Override
                    public void onReadyForSpeech(
                            Bundle params
                    ) {
                    }

                    @Override
                    public void onBeginningOfSpeech() {
                    }

                    @Override
                    public void onRmsChanged(
                            float rmsdB
                    ) {
                    }

                    @Override
                    public void onBufferReceived(
                            byte[] buffer
                    ) {
                    }

                    @Override
                    public void onEndOfSpeech() {
                    }

                    @Override
                    public void onPartialResults(
                            Bundle partialResults
                    ) {
                    }

                    @Override
                    public void onEvent(
                            int eventType,
                            Bundle params
                    ) {
                    }

                    @Override
                    public void onError(
                            int error
                    ) {

                        voiceMode = false;

                        recognizer.destroy();

                        resultMain.setText(
                                "Voice error. Type 4 digits."
                        );
                    }

                    @Override
                    public void onResults(
                            Bundle results
                    ) {

                        voiceMode = false;

                        ArrayList<String> heardList =
                                results.getStringArrayList(
                                        SpeechRecognizer.RESULTS_RECOGNITION
                                );

                        String heard =
                                heardList == null ||
                                        heardList.isEmpty()
                                        ? ""
                                        : heardList.get(0);

                        String q =
                                spokenDigits(
                                        heard
                                );

                        recognizer.destroy();

                        if (q.length() >= 4) {

                            q =
                                    q.substring(
                                            0,
                                            4
                                    );

                            suppressSearchWatcher = true;

                            search.setText(q);
                            search.selectAll();

                            suppressSearchWatcher = false;

                            performSearch(q);

                        } else {

                            resultMain.setText(
                                    "Heard: " +
                                            heard +
                                            ". Please say 4 digits."
                            );
                        }
                    }
                }
        );

        try {

            recognizer.startListening(
                    intent
            );

        } catch (Exception e) {

            voiceMode = false;

            recognizer.destroy();

            resultMain.setText(
                    "Unable to start voice search."
            );
        }
    }

    private String spokenDigits(
            String input
    ) {

        if (input == null) {
            return "";
        }

        String s =
                input.toLowerCase(
                        Locale.ROOT
                ).trim();

        StringBuilder out =
                new StringBuilder();

        String[] tokens =
                s.split(
                        "[^\\p{L}\\p{Nd}]+"
                );

        String[] words = {
                "zero",
                "one",
                "two",
                "three",
                "four",
                "five",
                "six",
                "seven",
                "eight",
                "nine",
                "शून्य",
                "एक",
                "दो",
                "तीन",
                "चार",
                "पाँच",
                "पांच",
                "छह",
                "सात",
                "आठ",
                "नौ"
        };

        for (String token : tokens) {

            boolean numeric = false;

            for (char c : token.toCharArray()) {

                if (
                        c >= '0' &&
                                c <= '9'
                ) {

                    out.append(c);

                    numeric = true;
                }
            }

            if (!numeric) {

                for (
                        int i = 0;
                        i < words.length;
                        i++
                ) {

                    if (
                            token.equals(
                                    words[i]
                            )
                    ) {

                        out.append(
                                i % 10
                        );

                        break;
                    }
                }
            }
        }

        return out.toString();
    }

    private void createNotificationChannel() {

        if (
                Build.VERSION.SDK_INT >=
                        Build.VERSION_CODES.O
        ) {

            NotificationChannel channel =
                    new NotificationChannel(
                            NOTIFICATION_CHANNEL,
                            "RecoveryOps Updates",
                            NotificationManager.IMPORTANCE_DEFAULT
                    );

            channel.setDescription(
                    "RecoveryOps file and system updates"
            );

            NotificationManager manager =
                    getSystemService(
                            NotificationManager.class
                    );

            if (manager != null) {

                manager.createNotificationChannel(
                        channel
                );
            }
        }
    }

    private void requestNotificationPermissionIfNeeded() {

        if (
                Build.VERSION.SDK_INT >= 33 &&
                        checkSelfPermission(
                                Manifest.permission.POST_NOTIFICATIONS
                        ) != PackageManager.PERMISSION_GRANTED
        ) {

            requestPermissions(
                    new String[]{
                            Manifest.permission.POST_NOTIFICATIONS
                    },
                    NOTIFICATION_REQ
            );
        }
    }

    private void pollNotifications() {

        if (
                !isLoggedIn() ||
                        !online
        ) {
            return;
        }

        authenticatedGet(
                "/notifications",
                new Callback() {

                    @Override
                    public void onFailure(
                            Call call,
                            IOException e
                    ) {
                    }

                    @Override
                    public void onResponse(
                            Call call,
                            Response response
                    ) throws IOException {

                        try (
                                Response ignored =
                                        response
                        ) {

                            if (response.code() == 401) {

                                handleAuthExpired();
                                return;
                            }

                            if (!response.isSuccessful()) {
                                return;
                            }

                            JSONArray arr =
                                    new JSONArray(
                                            response.body() == null
                                                    ? "[]"
                                                    : response.body().string()
                                    );

                            long lastSeen =
                                    prefs.getLong(
                                            "last_notification_id",
                                            0
                                    );

                            boolean initialized =
                                    prefs.getBoolean(
                                            "notifications_initialized",
                                            false
                                    );

                            long max = lastSeen;

                            for (
                                    int i = arr.length() - 1;
                                    i >= 0;
                                    i--
                            ) {

                                JSONObject n =
                                        arr.getJSONObject(i);

                                long id =
                                        n.optLong(
                                                "id",
                                                0
                                        );

                                if (id > max) {
                                    max = id;
                                }

                                if (
                                        initialized &&
                                                id > lastSeen
                                ) {

                                    showLocalNotification(n);
                                }
                            }

                            if (
                                    max > lastSeen ||
                                            !initialized
                            ) {

                                prefs.edit()
                                        .putLong(
                                                "last_notification_id",
                                                max
                                        )
                                        .putBoolean(
                                                "notifications_initialized",
                                                true
                                        )
                                        .apply();
                            }

                        } catch (Exception ignored) {
                        }
                    }
                }
        );
    }

    private void showLocalNotification(
            JSONObject n
    ) {

        if (
                Build.VERSION.SDK_INT >= 33 &&
                        checkSelfPermission(
                                Manifest.permission.POST_NOTIFICATIONS
                        ) != PackageManager.PERMISSION_GRANTED
        ) {

            return;
        }

        String title =
                n.optString(
                        "title",
                        "RecoveryOps Update"
                );

        String message =
                n.optString(
                        "message",
                        "New update available."
                );

        NotificationManager manager =
                (NotificationManager)
                        getSystemService(
                                Context.NOTIFICATION_SERVICE
                        );

        if (manager == null) {
            return;
        }

        NotificationCompat.Builder builder =
                new NotificationCompat.Builder(
                        this,
                        NOTIFICATION_CHANNEL
                )
                        .setSmallIcon(
                                R.drawable.recoveryops_icon
                        )
                        .setContentTitle(title)
                        .setContentText(message)
                        .setStyle(
                                new NotificationCompat.BigTextStyle()
                                        .bigText(message)
                        )
                        .setAutoCancel(true)
                        .setPriority(
                                NotificationCompat.PRIORITY_DEFAULT
                        );

        manager.notify(
                (int) (
                        n.optLong(
                                "id",
                                System.currentTimeMillis()
                        ) % Integer.MAX_VALUE
                ),
                builder.build()
        );
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode,
            String[] permissions,
            int[] grantResults
    ) {

        super.onRequestPermissionsResult(
                requestCode,
                permissions,
                grantResults
        );

        if (
                requestCode == AUDIO_REQ &&
                        (
                                grantResults.length == 0 ||
                                        grantResults[0] !=
                                                PackageManager.PERMISSION_GRANTED
                        )
        ) {

            resultMain.setText(
                    "Microphone permission is required for voice search."
            );
        }
    }

    @Override
    public void onBackPressed() {
        DrawerLayout drawer = findViewById(R.id.drawer);
        if (drawer != null && drawer.isDrawerOpen(Gravity.LEFT)) {
            drawer.closeDrawer(Gravity.LEFT);
            return;
        }

        if (!"home".equals(currentPageKey) && !pageHistory.isEmpty()) {
            PageState previous = pageHistory.remove(pageHistory.size() - 1);
            boolean oldRestoring = restoringPage;
            restoringPage = true;
            restorePage(previous);
            restoringPage = oldRestoring;
            return;
        }

        if (!"home".equals(currentPageKey)) {
            showHome();
            return;
        }

        if (backPressedOnceAt > 0 &&
                System.currentTimeMillis() - backPressedOnceAt < 1800) {
            finish();
            return;
        }

        backPressedOnceAt = System.currentTimeMillis();
        Toast.makeText(this, "Press back again to exit", Toast.LENGTH_SHORT).show();
    }

    private boolean restoringPage = false;
    private long backPressedOnceAt = 0L;

    static class LocalDb
            extends SQLiteOpenHelper {

        private static final String DB =
                "recoveryops_offline.db";

        LocalDb(
                Context context
        ) {

            super(
                    context,
                    DB,
                    null,
                    5
            );
        }

        @Override
        public void onConfigure(SQLiteDatabase db) {
            super.onConfigure(db);
            db.enableWriteAheadLogging();
        }

        @Override
        public void onCreate(
                SQLiteDatabase db
        ) {

            db.execSQL(
                    "CREATE TABLE vehicle(" +
                            "id INTEGER PRIMARY KEY," +
                            "registration_no TEXT," +
                            "registration_suffix TEXT," +
                            "chassis_no TEXT," +
                            "engine_no TEXT," +
                            "account_no TEXT," +
                            "loan_no TEXT," +
                            "customer_name TEXT," +
                            "mobile TEXT," +
                            "city TEXT," +
                            "state TEXT," +
                            "pincode TEXT," +
                            "vehicle_type TEXT," +
                            "bank_id INTEGER," +
                            "bank_name TEXT," +
                            "status TEXT," +
                            "version INTEGER," +
                            "source_file_id INTEGER," +
                            "raw_json TEXT)"
            );

            db.execSQL(
                    "CREATE INDEX idx_registration " +
                            "ON vehicle(registration_no)"
            );

            db.execSQL(
                    "CREATE INDEX idx_registration_suffix " +
                            "ON vehicle(registration_suffix)"
            );
        }

        @Override
        public void onUpgrade(
                SQLiteDatabase db,
                int oldVersion,
                int newVersion
        ) {

            try {

                db.execSQL(
                        "ALTER TABLE vehicle " +
                                "ADD COLUMN engine_no TEXT"
                );

            } catch (Exception ignored) {
            }

            try {

                db.execSQL(
                        "ALTER TABLE vehicle " +
                                "ADD COLUMN source_file_id INTEGER"
                );

            } catch (Exception ignored) {
            }

            try {

                db.execSQL(
                        "ALTER TABLE vehicle " +
                                "ADD COLUMN registration_suffix TEXT"
                );

            } catch (Exception ignored) {
            }

            try {

                db.execSQL(
                        "ALTER TABLE vehicle " +
                                "ADD COLUMN bank_name TEXT"
                );

            } catch (Exception ignored) {
            }

            try {

                db.execSQL(
                        "CREATE INDEX idx_registration " +
                                "ON vehicle(registration_no)"
                );

            } catch (Exception ignored) {
            }

            try {

                db.execSQL(
                        "CREATE INDEX idx_registration_suffix " +
                                "ON vehicle(registration_suffix)"
                );

            } catch (Exception ignored) {
            }
        }

        void clear() {

            getWritableDatabase()
                    .delete(
                            "vehicle",
                            null,
                            null
                    );
        }

        void upsert(
                JSONObject v
        ) throws JSONException {
            upsertOnDatabase(getWritableDatabase(), v);
        }

        void upsertBatch(
                JSONArray rows
        ) throws JSONException {

            if (rows == null || rows.length() == 0) {
                return;
            }

            SQLiteDatabase db = getWritableDatabase();
            db.beginTransaction();

            try {
                for (int i = 0; i < rows.length(); i++) {
                    upsertOnDatabase(db, rows.getJSONObject(i));
                }
                db.setTransactionSuccessful();
            } finally {
                db.endTransaction();
            }
        }

        private void upsertOnDatabase(
                SQLiteDatabase db,
                JSONObject v
        ) throws JSONException {

            ContentValues x = new ContentValues();

            int id = v.optInt("id", 0);
            if (id == 0) {
                id = Math.abs(v.toString().hashCode());
                if (id == 0) id = 1;
            }
            x.put("id", id);

            String reg = v.optString("registration_no", "");
            String regDigits = reg.replaceAll("[^0-9]", "");
            x.put("registration_suffix", regDigits.length() >= 4
                    ? regDigits.substring(regDigits.length() - 4) : "");

            String[] f = {
                    "registration_no", "chassis_no", "engine_no", "account_no",
                    "loan_no", "customer_name", "mobile", "city", "state",
                    "pincode", "vehicle_type", "status", "raw_json"
            };
            for (String k : f) x.put(k, v.optString(k, ""));

            x.put("bank_id", v.optInt("bank_id", 0));
            x.put("bank_name", v.optString("bank_name", ""));
            x.put("version", v.optInt("version", 1));
            x.put("source_file_id", v.optInt("source_file_id", 0));

            db.insertWithOnConflict(
                    "vehicle", null, x, SQLiteDatabase.CONFLICT_REPLACE
            );
        }

        int count() {

            Cursor c =
                    getReadableDatabase()
                            .rawQuery(
                                    "SELECT COUNT(*) FROM vehicle",
                                    null
                            );

            try {

                c.moveToFirst();

                return c.getInt(0);

            } finally {

                c.close();
            }
        }

        JSONArray searchRegistrationSuffix(
                String q
        ) {

            JSONArray out =
                    new JSONArray();

            if (
                    q == null ||
                            q.length() != 4
            ) {

                return out;
            }

            Cursor c =
                    getReadableDatabase()
                            .rawQuery(
                                    "SELECT * FROM vehicle " +
                                            "WHERE registration_suffix=? " +
                                            "ORDER BY registration_no " +
                                            "LIMIT 500",
                                    new String[]{
                                            q
                                    }
                            );

            try {

                while (c.moveToNext()) {

                    out.put(
                            row(c)
                    );
                }

            } finally {

                c.close();
            }

            return out;
        }

        JSONObject row(
                Cursor c
        ) {

            JSONObject o =
                    new JSONObject();

            String[] ns = {
                    "id",
                    "registration_no",
                    "registration_suffix",
                    "chassis_no",
                    "engine_no",
                    "account_no",
                    "loan_no",
                    "customer_name",
                    "mobile",
                    "city",
                    "state",
                    "pincode",
                    "vehicle_type",
                    "bank_id",
                    "bank_name",
                    "status",
                    "version",
                    "source_file_id",
                    "raw_json"
            };

            try {

                for (String k : ns) {

                    int i =
                            c.getColumnIndex(k);

                    if (i >= 0) {

                        o.put(
                                k,
                                c.getString(i)
                        );
                    }
                }

            } catch (Exception ignored) {
            }

            return o;
        }
    }
}