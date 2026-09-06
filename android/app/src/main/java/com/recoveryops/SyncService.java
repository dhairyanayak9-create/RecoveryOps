package com.recoveryops;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.NotificationCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;

public class SyncService extends Service {
    public static final String ACTION_PROGRESS = "com.recoveryops.SYNC_PROGRESS";
    public static final String EXTRA_PERCENT = "percent";
    public static final String EXTRA_DONE = "done";
    public static final String EXTRA_TOTAL = "total";
    public static final String EXTRA_COMPLETE = "complete";
    private static final String CHANNEL = "recoveryops_sync";
    private static final int NOTIFICATION_ID = 1207;

    private volatile boolean running = false;
    private MainActivity.LocalDb localDb;
    private SharedPreferences prefs;
    private OkHttpClient client;

    @Override
    public void onCreate() {
        super.onCreate();
        localDb = new MainActivity.LocalDb(this);
        prefs = getSharedPreferences("recoveryops", MODE_PRIVATE);
        client = new OkHttpClient.Builder()
                .connectTimeout(20, TimeUnit.SECONDS)
                .readTimeout(90, TimeUnit.SECONDS)
                .writeTimeout(30, TimeUnit.SECONDS)
                .retryOnConnectionFailure(true)
                .build();
        createChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (running) return START_STICKY;
        String token = prefs.getString("token", "").trim();
        if (token.isEmpty()) {
            stopSelf();
            return START_NOT_STICKY;
        }

        running = true;
        startForeground(NOTIFICATION_ID, notification("Syncing… 0%"));
        new Thread(() -> sync(token), "RecoveryOps-Sync").start();
        return START_STICKY;
    }

    private void sync(String token) {
        boolean downloadStarted = false;
        try {
            JSONObject dash = get(token, "/dashboard");
            if (dash == null) throw new IOException("Unable to reach server");
            int total = dash.optInt("accessible_total", dash.optInt("total", 0));

            int afterVersion = 0;
            int afterId = 0;
            boolean more = true;
            int safety = 0;
            int done = 0;

            // Do not erase an existing offline database until the first page
            // has been successfully received. A bad/empty server response
            // must never turn a working local DB into 0 records.
            while (more && running && safety++ < 100000) {
                JSONObject page = get(token,
                        "/sync/download?after_version=" + afterVersion +
                        "&after_id=" + afterId + "&limit=5000");
                if (page == null) throw new IOException("Sync request failed");

                if (!downloadStarted) {
                    int serverTotal = page.optInt("total", total);
                    JSONArray firstRows = page.optJSONArray("records");
                    if (serverTotal > 0 && (firstRows == null || firstRows.length() == 0)) {
                        throw new IOException("Server reported records but returned an empty sync page");
                    }
                    localDb.clear();
                    downloadStarted = true;
                    total = serverTotal;
                    broadcast(0, 0, total, false);
                    updateNotification("Syncing… 0%");
                }

                JSONArray rows = page.optJSONArray("records");
                if (rows != null && rows.length() > 0) {
                    localDb.upsertBatch(rows);
                    done += rows.length();
                }
                if (page.has("total")) total = page.optInt("total", total);

                afterVersion = page.optInt("next_version", afterVersion);
                afterId = page.optInt("next_id", afterId);
                more = page.optBoolean("has_more", false);

                int pct = total <= 0 ? (more ? 0 : 100)
                        : Math.min(100, Math.max(0, Math.round(done * 100f / total)));

                broadcast(pct, done, total, !more);
                updateNotification("Syncing… " + pct + "%");

                if (!more) break;
            }

            if (!downloadStarted) {
                throw new IOException("No sync data received from server");
            }

            int finalCount = localDb.count();
            if (total > 0 && finalCount == 0) {
                throw new IOException("Sync returned records but local database is empty");
            }

            prefs.edit().putLong("last_sync_at", System.currentTimeMillis()).apply();
            broadcast(100, finalCount, total, true);
            updateNotification("Sync complete • " + finalCount + " records");
        } catch (Exception e) {
            broadcast(-1, localDb.count(), 0, true);
            updateNotification("Sync failed");
        } finally {
            running = false;
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
        }
    }

    private JSONObject get(String token, String path) throws Exception {
        Request request = new Request.Builder()
                .url(MainActivity.SERVER_URL + "/api" + path)
                .header("Authorization", "Bearer " + token)
                .header("Accept", "application/json")
                .get()
                .build();

        try (Response response = client.newCall(request).execute()) {
            String body = response.body() == null ? "" : response.body().string();
            if (response.code() == 401) {
                Intent expired = new Intent(ACTION_PROGRESS)
                        .setPackage(getPackageName())
                        .putExtra(EXTRA_COMPLETE, true)
                        .putExtra(EXTRA_PERCENT, -2);
                sendBroadcast(expired);
                return null;
            }
            if (!response.isSuccessful()) {
                throw new IOException("HTTP " + response.code());
            }
            return new JSONObject(body);
        }
    }

    private void broadcast(int pct, int done, int total, boolean complete) {
        Intent i = new Intent(ACTION_PROGRESS);
        i.setPackage(getPackageName());
        i.putExtra(EXTRA_PERCENT, pct);
        i.putExtra(EXTRA_DONE, done);
        i.putExtra(EXTRA_TOTAL, total);
        i.putExtra(EXTRA_COMPLETE, complete);
        sendBroadcast(i);
    }

    private Notification notification(String text) {
        return new NotificationCompat.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.recoveryops_icon)
                .setContentTitle("RecoveryOps")
                .setContentText(text)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .build();
    }

    private void updateNotification(String text) {
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (manager != null) manager.notify(NOTIFICATION_ID, notification(text));
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel c = new NotificationChannel(
                    CHANNEL, "RecoveryOps Sync", NotificationManager.IMPORTANCE_LOW);
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) manager.createNotificationChannel(c);
        }
    }

    @Override
    public void onDestroy() {
        running = false;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}