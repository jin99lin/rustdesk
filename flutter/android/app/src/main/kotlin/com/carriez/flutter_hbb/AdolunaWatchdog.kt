package com.carriez.flutter_hbb

// b85: alarm-driven watchdog. Fires about once a minute; if the remote-support service is not running
// (process was killed, crashed, or the service stopped) it re-starts MainService the same way boot does.
// Only on Device Owner devices or when "start on boot" is on, so a user who turned the service off is respected.
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import io.flutter.embedding.android.FlutterActivity

class AdolunaWatchdog : BroadcastReceiver() {
    override fun onReceive(c: Context, intent: Intent) {
        AdolunaDevice.scheduleWatchdog(c)
        val owner = AdolunaDO.isOwner(c)
        val onBoot = c.getSharedPreferences(KEY_SHARED_PREFERENCES, FlutterActivity.MODE_PRIVATE).getBoolean(KEY_START_ON_BOOT_OPT, true)
        if (!owner && !onBoot) return
        if (MainService.isReady) return
        Log.w("AdolunaDO", "watchdog: MainService not running (isReady=${MainService.isReady}), restarting")
        AdolunaDO.ensureService(c, "watchdog", 500)
    }
}
