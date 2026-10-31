package com.carriez.flutter_hbb

// Adoluna b79: Device Owner core helpers (setup, kiosk, install). Backend commands live in AdolunaDevice.kt.
// The TEST_* broadcasts stay for staff via adb (protected by android.permission.DUMP).
import android.app.Activity
import android.app.ActivityManager
import android.app.ActivityOptions
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.app.PendingIntent
import android.app.admin.DeviceAdminReceiver
import android.app.admin.DevicePolicyManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.util.Log
import java.io.File
import java.io.InputStream
import java.net.URL

private const val TAG = "AdolunaDO"
const val ACT_TEST_INSTALL = "com.adoluna.support.TEST_INSTALL"
const val ACT_TEST_SETUP = "com.adoluna.support.TEST_SETUP"
const val ACT_TEST_KIOSK = "com.adoluna.support.TEST_KIOSK"
const val ACT_TEST_KIOSK_CLEAR = "com.adoluna.support.TEST_KIOSK_CLEAR"
const val ACT_TEST_CLEAR_DO = "com.adoluna.support.TEST_CLEAR_DO"
const val ACT_INSTALL_RESULT = "com.adoluna.support.INSTALL_RESULT"
private const val PREFS = "adoluna_do"
private const val KEY_KIOSK = "kiosk_pkg"

class AdolunaAdminReceiver : DeviceAdminReceiver() {
    override fun onEnabled(context: Context, intent: Intent) {
        Log.i(TAG, "admin enabled")
        AdolunaDO.setup(context)
    }

    override fun onProfileProvisioningComplete(context: Context, intent: Intent) {
        AdolunaDO.setup(context)
    }
}

object AdolunaDO {
    fun admin(c: Context) = ComponentName(c, AdolunaAdminReceiver::class.java)
    fun dpm(c: Context) = c.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
    fun isOwner(c: Context) = dpm(c).isDeviceOwnerApp(c.packageName)

    /** Grant own runtime permissions, allow our accessibility service, keep app enabled. */
    fun setup(c: Context) {
        if (!isOwner(c)) { Log.w(TAG, "setup: not device owner"); return }
        val d = dpm(c); val a = admin(c)
        val perms = mutableListOf(android.Manifest.permission.RECORD_AUDIO, android.Manifest.permission.READ_PHONE_STATE)
        if (Build.VERSION.SDK_INT >= 33) perms += "android.permission.POST_NOTIFICATIONS"
        for (p in perms) try {
            val ok = d.setPermissionGrantState(a, c.packageName, p, DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED)
            Log.i(TAG, "grant $p -> $ok")
        } catch (e: Exception) { Log.w(TAG, "grant $p failed: $e") }
        try { d.setPermittedAccessibilityServices(a, null) } catch (e: Exception) { Log.w(TAG, "a11y allow: $e") }
        try { d.setApplicationHidden(a, c.packageName, false) } catch (e: Exception) { }
        Log.i(TAG, "setup done")
    }

    fun kioskPkg(c: Context): String? = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_KIOSK, null)

    fun lockState(c: Context): Int {
        val am = c.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        return if (Build.VERSION.SDK_INT >= 23) am.lockTaskModeState else -1
    }

    /** Allowlist own pkg + target, disable lock-task features, verify, then hand off to AdolunaKioskActivity. */
    fun allowKiosk(c: Context, pkg: String): Boolean {
        val d = dpm(c); val a = admin(c)
        try { d.setLockTaskPackages(a, arrayOf(c.packageName, pkg, "com.android.systemui")) } catch (e: Exception) { Log.e(TAG, "kiosk: setLockTaskPackages failed: $e"); return false }
        if (Build.VERSION.SDK_INT >= 28) try {
            d.setLockTaskFeatures(a, DevicePolicyManager.LOCK_TASK_FEATURE_NONE)
        } catch (e: Exception) { Log.w(TAG, "kiosk: setLockTaskFeatures: $e") }
        val pkgs = try { d.getLockTaskPackages(a).joinToString(",") } catch (e: Exception) { "err $e" }
        Log.i(TAG, "kiosk: lockTaskPackages=[$pkgs] permitted(self)=${d.isLockTaskPermitted(c.packageName)} permitted($pkg)=${d.isLockTaskPermitted(pkg)}")
        return d.isLockTaskPermitted(pkg)
    }

    /** Re-init MainService media projection (+ server/hbbs) if capture permission was lost. */
    fun ensureService(c: Context, why: String, delayMs: Long = 3000) {
        Handler(Looper.getMainLooper()).postDelayed({
            Log.i(TAG, "svc check ($why): isReady=${MainService.isReady} isStart=${MainService.isStart} lockState=${lockState(c)}")
            if (MainService.isReady) return@postDelayed
            try {
                val i = Intent(c, MainService::class.java).setAction(ACT_INIT_MEDIA_PROJECTION_AND_SERVICE)
                    .putExtra(EXT_INIT_FROM_BOOT, true)
                if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i) else c.startService(i)
                Log.i(TAG, "svc ($why): re-init MainService requested")
            } catch (e: Exception) { Log.e(TAG, "svc ($why): re-init failed: $e") }
        }, delayMs)
    }

    fun startKiosk(c: Context, pkg: String) {
        if (!isOwner(c)) { Log.w(TAG, "kiosk: not device owner"); return }
        c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY_KIOSK, pkg).apply()
        launchKiosk(c)
    }

    fun launchKiosk(c: Context) {
        val pkg = kioskPkg(c) ?: return
        if (!isOwner(c)) return
        if (!allowKiosk(c, pkg)) Log.w(TAG, "kiosk: $pkg not permitted after allowlist")
        try {
            val i = Intent(c, AdolunaKioskActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                .putExtra("pkg", pkg)
            if (Build.VERSION.SDK_INT >= 28)
                c.startActivity(i, ActivityOptions.makeBasic().setLockTaskEnabled(true).toBundle())
            else c.startActivity(i)
            Log.i(TAG, "kiosk: started AdolunaKioskActivity for $pkg")
            AdolunaDevice.showHotspot(c)
        } catch (e: Exception) { Log.e(TAG, "kiosk: start kiosk activity failed: $e") }
    }

    /** Fallback/timed unlock: leave lock task but keep the kiosk app remembered for relock. */
    fun stopLockKeepPkg(c: Context) {
        AdolunaDevice.hideHotspot(c)
        if (lockState(c) != 0) try {
            c.startActivity(Intent(c, AdolunaKioskActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("stop", true))
        } catch (e: Exception) { Log.w(TAG, "kiosk pause: $e") }
        ensureService(c, "kiosk-pause")
    }

    fun clearKiosk(c: Context) {
        AdolunaDevice.hideHotspot(c)
        c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(KEY_KIOSK).apply()
        if (lockState(c) != 0) try {
            c.startActivity(Intent(c, AdolunaKioskActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("stop", true))
        } catch (e: Exception) { Log.w(TAG, "kiosk clear: stop activity: $e") }
        if (isOwner(c)) try { dpm(c).setLockTaskPackages(admin(c), arrayOf()) } catch (e: Exception) { Log.w(TAG, "kiosk clear: $e") }
        Handler(Looper.getMainLooper()).postDelayed({ Log.i(TAG, "kiosk cleared, lockTaskModeState=${lockState(c)}") }, 1500)
        ensureService(c, "kiosk-clear")
    }

    fun clearOwner(c: Context) {
        if (!isOwner(c)) { Log.w(TAG, "clear: not device owner"); return }
        clearKiosk(c)
        try { dpm(c).setPermittedAccessibilityServices(admin(c), null) } catch (_: Exception) {}
        @Suppress("DEPRECATION") dpm(c).clearDeviceOwnerApp(c.packageName)
        Log.i(TAG, "device owner cleared")
    }

    /** Silent install via PackageInstaller session (silent only when we are Device Owner). */
    fun install(c: Context, path: String?, url: String?) {
        Thread {
            try {
                val input: InputStream = when {
                    !url.isNullOrEmpty() -> URL(url).openStream()
                    !path.isNullOrEmpty() -> File(path).inputStream()
                    else -> File(c.getExternalFilesDir(null), "update.apk").inputStream()
                }
                val pi = c.packageManager.packageInstaller
                val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
                val id = pi.createSession(params)
                pi.openSession(id).use { s ->
                    s.openWrite("base.apk", 0, -1).use { out -> input.use { it.copyTo(out) }; s.fsync(out) }
                    val cb = Intent(c, AdolunaInstallResult::class.java).setAction(ACT_INSTALL_RESULT)
                    var fl = PendingIntent.FLAG_UPDATE_CURRENT
                    if (Build.VERSION.SDK_INT >= 31) fl = fl or PendingIntent.FLAG_MUTABLE
                    s.commit(PendingIntent.getBroadcast(c, id, cb, fl).intentSender)
                }
                Log.i(TAG, "install session $id committed (owner=${isOwner(c)})")
            } catch (e: Exception) { Log.e(TAG, "install failed: $e") }
        }.start()
    }
}

/** Test-only triggers. Protected by android.permission.DUMP so only adb shell / system can send. */
class AdolunaDOReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        Log.i(TAG, "onReceive ${i.action}")
        when (i.action) {
            ACT_TEST_SETUP -> AdolunaDO.setup(c)
            ACT_TEST_INSTALL -> AdolunaDO.install(c, i.getStringExtra("path"), i.getStringExtra("url"))
            ACT_TEST_KIOSK -> i.getStringExtra("pkg")?.let { AdolunaDO.startKiosk(c, it) } ?: Log.w(TAG, "missing --es pkg")
            ACT_TEST_KIOSK_CLEAR -> AdolunaDO.clearKiosk(c)
            ACT_TEST_CLEAR_DO -> AdolunaDO.clearOwner(c)
        }
    }
}

class AdolunaInstallResult : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
                val st = i.getIntExtra(PackageInstaller.EXTRA_STATUS, -999)
                Log.i(TAG, "install result status=$st msg=${i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)}")
                val n = i.getLongExtra("n", 0)
                if (n > 0 && st != PackageInstaller.STATUS_PENDING_USER_ACTION)
                    AdolunaDevice.ack(n, st == PackageInstaller.STATUS_SUCCESS, "install result $st ${i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: ""}")
                if (st != PackageInstaller.STATUS_PENDING_USER_ACTION) AdolunaDevice.onInstallDone(c)
                if (st == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                    @Suppress("DEPRECATION") val confirm = i.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                    confirm?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)?.let { c.startActivity(it) }
                }
            
    }
}

/** Test-only, invisible. Enters lock task itself, then launches the allowlisted target which joins lock task mode.
 *  Stays alive (behind target) so the lock task keeps a root; "stop" extra calls stopLockTask(). */
class AdolunaKioskActivity : Activity() {
    companion object {
        @Volatile var instance: AdolunaKioskActivity? = null
        @Volatile var permissionInFlight = false
    }
    private var launched = false
    override fun onCreate(b: Bundle?) { super.onCreate(b); instance = this; handle(intent) }
    override fun onDestroy() { if (instance === this) instance = null; Log.i(TAG, "kiosk activity destroyed"); super.onDestroy() }
    override fun onNewIntent(i: Intent) { super.onNewIntent(i); setIntent(i); launched = false; handle(i) }

    private fun handle(i: Intent) {
        if (i.getBooleanExtra("stop", false)) {
            try { stopLockTask(); Log.i(TAG, "kiosk: stopLockTask called") } catch (e: Exception) { Log.w(TAG, "kiosk: stopLockTask: $e") }
            finishAndRemoveTask(); return
        }
        val pkg = i.getStringExtra("pkg") ?: AdolunaDO.kioskPkg(this)
        if (pkg == null) { finish(); return }
        // Make sure screen capture is granted BEFORE locking, so no consent activity is needed at connect.
        if (!MainService.isReady) { Log.i(TAG, "kiosk: capture not ready, requesting before lock"); AdolunaDO.ensureService(this, "kiosk-prelock", 0) }
        waitReadyThenLock(pkg, 0)
    }

    private fun waitReadyThenLock(pkg: String, n: Int) {
        if (!MainService.isReady && n < 16) { Handler(Looper.getMainLooper()).postDelayed({ waitReadyThenLock(pkg, n + 1) }, 500); return }
        Log.i(TAG, "kiosk: capture isReady=${MainService.isReady} after ${n * 500}ms, locking")
        try { startLockTask(); Log.i(TAG, "kiosk: startLockTask ok, state=${AdolunaDO.lockState(this)}") }
        catch (e: Exception) { Log.e(TAG, "kiosk: startLockTask failed: $e") }
        Handler(Looper.getMainLooper()).postDelayed({ launchTarget(pkg) }, 400)
        AdolunaDO.ensureService(this, "kiosk-start", 4000)
    }

    private fun launchTarget(pkg: String) {
        if (launched) return; launched = true
        val t = packageManager.getLaunchIntentForPackage(pkg)
            ?: packageManager.getLeanbackLaunchIntentForPackage(pkg)
        if (t == null) { Log.w(TAG, "kiosk: no launcher activity for $pkg (lock stays on our blank screen)"); return }
        t.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            if (Build.VERSION.SDK_INT >= 28) startActivity(t, ActivityOptions.makeBasic().setLockTaskEnabled(true).toBundle())
            else startActivity(t)
            Log.i(TAG, "kiosk launched $pkg")
        } catch (e: Exception) { Log.e(TAG, "kiosk launch failed: $e") }
        Handler(Looper.getMainLooper()).postDelayed({
            Log.i(TAG, "kiosk: after launch lockTaskModeState=${AdolunaDO.lockState(this)} (0=NONE 1=LOCKED 2=PINNED)")
        }, 2000)
    }

    // If user lands back here while locked (target closed), relaunch target.
    override fun onResume() {
        super.onResume()
        if (launched && AdolunaDO.lockState(this) != 0) {
            val pkg = AdolunaDO.kioskPkg(this) ?: return
            Handler(Looper.getMainLooper()).postDelayed({
                if (permissionInFlight) { Log.i(TAG, "kiosk: permission activity in flight, not reopening target"); return@postDelayed }
                if (!hasWindowFocus() && instance !== this) return@postDelayed
                launched = false; launchTarget(pkg)
            }, 1500)
        }
    }
}
