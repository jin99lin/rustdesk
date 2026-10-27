package com.carriez.flutter_hbb

import android.Manifest.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS
import android.Manifest.permission.SYSTEM_ALERT_WINDOW
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import android.widget.Toast
import com.hjq.permissions.XXPermissions
import io.flutter.embedding.android.FlutterActivity

const val DEBUG_BOOT_COMPLETED = "com.carriez.flutter_hbb.DEBUG_BOOT_COMPLETED"

class BootReceiver : BroadcastReceiver() {
    private val logTag = "tagBootReceiver"

    companion object {
        @Volatile
        var started = false
    }

    override fun onReceive(context: Context, intent: Intent) {
        Log.d(logTag, "onReceive ${intent.action}")

        val bootActions = setOf(
            Intent.ACTION_BOOT_COMPLETED, DEBUG_BOOT_COMPLETED,
            "android.intent.action.QUICKBOOT_POWERON", "com.htc.intent.action.QUICKBOOT_POWERON",
            "android.intent.action.REBOOT", Intent.ACTION_MY_PACKAGE_REPLACED, Intent.ACTION_USER_PRESENT
        )
        if (intent.action in bootActions) {
            // Adoluna: fallback actions may fire repeatedly; only start once per process.
            if (started || MainService.isStart) {
                Log.d(logTag, "already started by boot receiver")
                return
            }
            // check SharedPreferences config
            val prefs = context.getSharedPreferences(KEY_SHARED_PREFERENCES, FlutterActivity.MODE_PRIVATE)
            if (!prefs.getBoolean(KEY_START_ON_BOOT_OPT, true)) {
                Log.d(logTag, "KEY_START_ON_BOOT_OPT is false")
                return
            }
            // check pre-permission
            // Adoluna: overlay (SYSTEM_ALERT_WINDOW) is only needed on Android 10+ to launch the
            // screen-capture consent activity from background. Below Android 10 (e.g. Sunmi K2_MINI)
            // background activity starts are allowed, so don't require it (floating window is off).
            // Adoluna: some ROMs misreport this; log only, start anyway.
            if (!XXPermissions.isGranted(context, REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)) {
                Log.w(logTag, "REQUEST_IGNORE_BATTERY_OPTIMIZATIONS is not granted, starting anyway")
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
                !XXPermissions.isGranted(context, SYSTEM_ALERT_WINDOW)) {
                Log.d(logTag, "SYSTEM_ALERT_WINDOW is not granted (required on Android 10+)")
                return
            }

            val it = Intent(context, MainService::class.java).apply {
                action = ACT_INIT_MEDIA_PROJECTION_AND_SERVICE
                putExtra(EXT_INIT_FROM_BOOT, true)
            }
            started = true
            Toast.makeText(context, "Adoluna Support is Open", Toast.LENGTH_LONG).show()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(it)
            } else {
                context.startService(it)
            }
        }
    }
}
