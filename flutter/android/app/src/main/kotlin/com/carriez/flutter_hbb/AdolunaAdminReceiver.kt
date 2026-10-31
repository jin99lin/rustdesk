package com.carriez.flutter_hbb

// Adoluna DO TEST build (b78-do1): Device Owner helpers. NOT for merchant release.
import android.app.ActivityOptions
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
        val perms = mutableListOf(android.Manifest.permission.RECORD_AUDIO)
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

    fun startKiosk(c: Context, pkg: String) {
        if (!isOwner(c)) { Log.w(TAG, "kiosk: not device owner"); return }
        dpm(c).setLockTaskPackages(admin(c), arrayOf(c.packageName, pkg))
        c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY_KIOSK, pkg).apply()
        launchKiosk(c)
    }

    fun launchKiosk(c: Context) {
        val pkg = kioskPkg(c) ?: return
        if (!isOwner(c)) return
        val i = c.packageManager.getLaunchIntentForPackage(pkg)
        if (i == null) { Log.w(TAG, "kiosk: no launcher activity for $pkg"); return }
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            if (Build.VERSION.SDK_INT >= 28) {
                val o = ActivityOptions.makeBasic().setLockTaskEnabled(true)
                c.startActivity(i, o.toBundle())
            } else c.startActivity(i)
            Log.i(TAG, "kiosk launched $pkg")
        } catch (e: Exception) { Log.w(TAG, "kiosk launch failed: $e") }
    }

    fun clearKiosk(c: Context) {
        c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(KEY_KIOSK).apply()
        if (isOwner(c)) dpm(c).setLockTaskPackages(admin(c), arrayOf())
        Log.i(TAG, "kiosk cleared")
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
                if (st == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                    @Suppress("DEPRECATION") val confirm = i.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                    confirm?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)?.let { c.startActivity(it) }
                }
            
    }
}
