package com.carriez.flutter_hbb

// Adoluna b79: Device Owner productization - backend commands, policies, device facts,
// fallback unlock (corner gesture + per-device code / one-time code), timed unlock, safe boot relock.
// Devices that are NOT Device Owner only get: info, install/update (with system prompt), ping.
import android.app.Activity
import android.app.PendingIntent
import android.app.admin.DevicePolicyManager
import android.app.admin.SystemUpdatePolicy
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageInstaller
import android.graphics.Color
import android.graphics.PixelFormat
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.StatFs
import android.os.SystemClock
import android.os.UserManager
import android.provider.Settings
import android.text.InputType
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.URL
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

private const val TAG = "AdolunaDO"
private const val PREFS = "adoluna_do"

object AdolunaDevice {
    private val main = Handler(Looper.getMainLooper())
    private val acks = mutableListOf<JSONObject>()
    private fun prefs(c: Context) = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    // ---------------- acks (read by Rust heartbeat) ----------------
    @Synchronized fun ack(n: Long, ok: Boolean, msg: String) {
        Log.i(TAG, "cmd #$n ok=$ok $msg")
        acks.removeAll { it.optLong("n") == n }
        acks.add(JSONObject().put("n", n).put("ok", ok).put("msg", msg.take(400)))
        while (acks.size > 20) acks.removeAt(0)
    }
    @Synchronized fun acksJson(): String = JSONArray(acks.map { JSONObject(it.toString()) }).toString()
    @Synchronized fun clearAcks(sent: String) {
        val ns = try { val a = JSONArray(sent); (0 until a.length()).map { a.getJSONObject(it).optLong("n") }.toSet() } catch (e: Exception) { emptySet() }
        acks.removeAll { it.optLong("n") in ns }
    }

    // ---------------- device facts ----------------
    fun ownBuild(c: Context): Long = try {
        val pi = c.packageManager.getPackageInfo(c.packageName, 0)
        if (Build.VERSION.SDK_INT >= 28) pi.longVersionCode else @Suppress("DEPRECATION") pi.versionCode.toLong()
    } catch (e: Exception) { 0L }
    fun ownVersionName(c: Context): String = try { c.packageManager.getPackageInfo(c.packageName, 0).versionName ?: "" } catch (e: Exception) { "" }

    fun info(c: Context): String {
        val o = JSONObject()
        val owner = AdolunaDO.isOwner(c)
        o.put("platform", "Android").put("is_owner", owner)
        o.put("model", "${Build.MANUFACTURER} ${Build.MODEL}").put("os", "Android ${Build.VERSION.RELEASE}").put("sdk", Build.VERSION.SDK_INT)
        o.put("serial", serial(c))
        o.put("ver", ownVersionName(c)).put("build", ownBuild(c))
        try {
            val b = c.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            if (b != null) {
                val l = b.getIntExtra(BatteryManager.EXTRA_LEVEL, -1); val s = b.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
                if (l >= 0) o.put("battery", l * 100 / s)
                o.put("charging", b.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0)
            }
        } catch (_: Exception) {}
        try {
            val st = StatFs(Environment.getDataDirectory().path)
            o.put("disk_free_gb", Math.round(st.availableBytes / 1e8) / 10.0).put("disk_total_gb", Math.round(st.totalBytes / 1e8) / 10.0)
        } catch (_: Exception) {}
        o.put("ready", MainService.isReady)
        o.put("kiosk", AdolunaDO.kioskPkg(c) ?: "").put("lock_state", AdolunaDO.lockState(c))
        val until = prefs(c).getLong("unlock_until", 0)
        if (until > System.currentTimeMillis()) o.put("unlocked_until", until / 1000)
        o.put("kiosk_pending", pendingRelock)
        o.put("policy", JSONObject(prefs(c).getString("policy", "{}") ?: "{}"))
        o.put("unlock_code_set", prefs(c).contains("uc_h")).put("otp_set", prefs(c).contains("otp_k"))
        try { o.put("apps_hash", appsHash(c)) } catch (_: Exception) {}
        return o.toString()
    }

    // ---------------- b81: launcher app list (for the backend's kiosk app picker) ----------------
    @Volatile private var pendingApps: String? = null
    @Synchronized fun takeApps(): String { val s = pendingApps ?: ""; pendingApps = null; return s }
    private fun launcherApps(c: Context): List<android.content.pm.ResolveInfo> {
        val i = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val skip = setOf(c.packageName, "com.android.settings")
        return c.packageManager.queryIntentActivities(i, 0).filter { it.activityInfo.packageName !in skip }
            .distinctBy { it.activityInfo.packageName }
    }
    private fun verOf(c: Context, pkg: String): String = try { c.packageManager.getPackageInfo(pkg, 0).versionName ?: "" } catch (e: Exception) { "" }
    private fun vcOf(c: Context, pkg: String): Long = try {
        val pi = c.packageManager.getPackageInfo(pkg, 0)
        if (Build.VERSION.SDK_INT >= 28) pi.longVersionCode else @Suppress("DEPRECATION") pi.versionCode.toLong()
    } catch (e: Exception) { -1L }
    fun appsHash(c: Context): String {
        val s = launcherApps(c).map { it.activityInfo.packageName + "@" + verOf(c, it.activityInfo.packageName) }.sorted().joinToString("\n")
        return MessageDigest.getInstance("SHA-256").digest(s.toByteArray()).take(8).joinToString("") { "%02x".format(it) }
    }
    private fun iconB64(c: Context, ri: android.content.pm.ResolveInfo): String = try {
        val d = ri.loadIcon(c.packageManager)
        val bmp = android.graphics.Bitmap.createBitmap(48, 48, android.graphics.Bitmap.Config.ARGB_8888)
        val cv = android.graphics.Canvas(bmp); d.setBounds(0, 0, 48, 48); d.draw(cv)
        val bo = java.io.ByteArrayOutputStream(); bmp.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, bo)
        if (bo.size() > 6000) "" else android.util.Base64.encodeToString(bo.toByteArray(), android.util.Base64.NO_WRAP)
    } catch (e: Exception) { "" }
    fun listApps(c: Context): Int {
        val arr = JSONArray(); var total = 0
        for (ri in launcherApps(c).sortedBy { it.loadLabel(c.packageManager).toString().lowercase() }.take(300)) {
            val pkg = ri.activityInfo.packageName
            val o = JSONObject().put("name", ri.loadLabel(c.packageManager).toString().take(80)).put("pkg", pkg).put("ver", verOf(c, pkg).take(40)).put("vc", vcOf(c, pkg))
            if (total < 400_000) { val ic = iconB64(c, ri); if (ic.isNotEmpty()) { o.put("icon", ic); total += ic.length } }
            arr.put(o)
        }
        pendingApps = JSONObject().put("hash", appsHash(c)).put("apps", arr).toString()
        return arr.length()
    }

    private fun serial(c: Context): String = try {
        if (Build.VERSION.SDK_INT >= 26) Build.getSerial() else @Suppress("DEPRECATION") Build.SERIAL
    } catch (e: Exception) { "" }

    // ---------------- commands ----------------
    fun handleCommand(c: Context, arg: String) {
        val j = try { JSONObject(arg) } catch (e: Exception) { return }
        val n = j.optLong("n"); val k = j.optString("k"); val p = j.optJSONObject("p") ?: JSONObject()
        val owner = AdolunaDO.isOwner(c)
        j.optString("id").takeIf { it.isNotEmpty() }?.let { prefs(c).edit().putString("dev_id", it).apply() }
        fun needOwner(): Boolean { if (!owner) ack(n, false, "not device owner"); return owner }
        try {
            when (k) {
                "ping", "info" -> ack(n, true, "ok")
                "list_apps" -> ack(n, true, "apps: ${listApps(c)}")
                "install" -> install(c, n, p)
                "update" -> {
                    // compare build numbers (versionCode) only, never the version string
                    val want = p.optLong("build", 0L); val own = ownBuild(c)
                    if (want <= 0L) ack(n, false, "update needs a numeric build number")
                    else if (want <= own) ack(n, true, "already up to date (b$own)")
                    else install(c, n, p)
                }
                "reboot" -> if (needOwner()) {
                    if (Build.VERSION.SDK_INT < 24) ack(n, false, "reboot needs Android 7+")
                    else { ack(n, true, "rebooting"); main.postDelayed({ AdolunaDO.dpm(c).reboot(AdolunaDO.admin(c)) }, 20000) }
                }
                "kiosk_lock" -> if (needOwner()) {
                    val pkg = p.optString("pkg")
                    if (pkg.isEmpty() || c.packageManager.getLaunchIntentForPackage(pkg) == null) ack(n, false, "app not installed: $pkg")
                    else { prefs(c).edit().remove("unlock_until").apply(); AdolunaDO.startKiosk(c, pkg); ack(n, true, "locked to $pkg") }
                }
                "kiosk_unlock" -> if (needOwner()) {
                    prefs(c).edit().remove("unlock_until").apply(); pendingRelock = false
                    hideFloat(c); AdolunaDO.clearKiosk(c); ack(n, true, "unlocked")
                }
                "policy" -> if (needOwner()) { applyPolicy(c, p); prefs(c).edit().putString("policy", p.toString()).apply(); ack(n, true, "policy applied") }
                "unlock_code" -> {
                    prefs(c).edit().putString("uc_h", p.optString("h")).putString("uc_s", p.optString("s")).apply(); ack(n, true, "unlock code set")
                }
                "otp_secret" -> { prefs(c).edit().putString("otp_k", p.optString("k")).apply(); ack(n, true, "one-time code secret set") }
                else -> ack(n, false, "unsupported: $k")
            }
        } catch (e: Exception) { ack(n, false, "$e") }
    }

    // ---------------- install / update ----------------
    private fun install(c: Context, n: Long, p: JSONObject) {
        val url = p.optString("url"); val want = p.optString("sha256").lowercase()
        if (url.isEmpty() || want.length != 64) { ack(n, false, "url and sha256 required"); return }
        Thread {
            try {
                val f = File(c.cacheDir, "adoluna-dl.apk")
                URL(url).openStream().use { i -> f.outputStream().use { o -> i.copyTo(o) } }
                val md = MessageDigest.getInstance("SHA-256")
                f.inputStream().use { i -> val buf = ByteArray(65536); while (true) { val r = i.read(buf); if (r <= 0) break; md.update(buf, 0, r) } }
                val got = md.digest().joinToString("") { "%02x".format(it) }
                if (got != want) { f.delete(); ack(n, false, "sha256 mismatch"); return@Thread }
                // b81: skip when the same or a newer version of this app is already installed (unless forced)
                if (!p.optBoolean("force", false)) try {
                    val ai = c.packageManager.getPackageArchiveInfo(f.path, 0)
                    if (ai != null) {
                        val nv = if (Build.VERSION.SDK_INT >= 28) ai.longVersionCode else @Suppress("DEPRECATION") ai.versionCode.toLong()
                        val have = vcOf(c, ai.packageName)
                        if (have >= 0 && have >= nv) { f.delete(); ack(n, true, "up to date: ${ai.packageName} vc$have >= $nv"); return@Thread }
                    }
                } catch (_: Exception) {}
                val owner = AdolunaDO.isOwner(c)
                // our own "block installs" restriction must not stop the backend's install
                if (owner) try { AdolunaDO.dpm(c).clearUserRestriction(AdolunaDO.admin(c), UserManager.DISALLOW_INSTALL_APPS) } catch (_: Exception) {}
                val pi = c.packageManager.packageInstaller
                val id = pi.createSession(PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL))
                pi.openSession(id).use { s ->
                    s.openWrite("base.apk", 0, f.length()).use { out -> f.inputStream().use { it.copyTo(out) }; s.fsync(out) }
                    val cb = Intent(c, AdolunaInstallResult::class.java).setAction(ACT_INSTALL_RESULT).putExtra("n", n)
                    var fl = PendingIntent.FLAG_UPDATE_CURRENT
                    if (Build.VERSION.SDK_INT >= 31) fl = fl or PendingIntent.FLAG_MUTABLE
                    s.commit(PendingIntent.getBroadcast(c, id, cb, fl).intentSender)
                }
                f.delete()
                ack(n, true, if (owner) "installing silently" else "install prompt shown to user")
            } catch (e: Exception) { ack(n, false, "install failed: $e") }
        }.start()
    }

    fun onInstallDone(c: Context) {
        // re-apply policy (restores DISALLOW_INSTALL_APPS if the policy asks for it)
        if (AdolunaDO.isOwner(c)) try { applyPolicy(c, JSONObject(prefs(c).getString("policy", "{}") ?: "{}")) } catch (_: Exception) {}
    }

    // ---------------- policies ----------------
    fun applyPolicy(c: Context, p: JSONObject) {
        val d = AdolunaDO.dpm(c); val a = AdolunaDO.admin(c)
        fun r(key: String, on: Boolean) = try { if (on) d.addUserRestriction(a, key) else d.clearUserRestriction(a, key) } catch (e: Exception) { Log.w(TAG, "restriction $key: $e") }
        try { d.setUninstallBlocked(a, c.packageName, p.optBoolean("no_uninstall", true)) } catch (e: Exception) { Log.w(TAG, "uninstall block: $e") }
        val apps = p.optBoolean("block_apps"); r(UserManager.DISALLOW_INSTALL_APPS, apps); r(UserManager.DISALLOW_UNINSTALL_APPS, apps)
        r(UserManager.DISALLOW_INSTALL_UNKNOWN_SOURCES, apps)
        r(UserManager.DISALLOW_FACTORY_RESET, p.optBoolean("block_factory_reset"))
        val s = p.optBoolean("block_settings")
        r(UserManager.DISALLOW_CONFIG_WIFI, s); r(UserManager.DISALLOW_CONFIG_BLUETOOTH, s); r(UserManager.DISALLOW_SAFE_BOOT, s)
        if (Build.VERSION.SDK_INT >= 28) r(UserManager.DISALLOW_CONFIG_DATE_TIME, s)
        // USB debugging / developer options: separate switch so staff can keep adb as a last resort
        r(UserManager.DISALLOW_DEBUGGING_FEATURES, p.optBoolean("block_debug"))
        if (p.optBoolean("screen_on")) {
            try { d.setGlobalSetting(a, Settings.Global.STAY_ON_WHILE_PLUGGED_IN, "7") } catch (e: Exception) { Log.w(TAG, "stay on: $e") }
            if (Build.VERSION.SDK_INT >= 28) try { d.setSystemSetting(a, Settings.System.SCREEN_OFF_TIMEOUT, "2147483647") } catch (e: Exception) { Log.w(TAG, "timeout: $e") }
            if (Build.VERSION.SDK_INT >= 23) try { d.setKeyguardDisabled(a, true) } catch (_: Exception) {}
        } else try { d.setGlobalSetting(a, Settings.Global.STAY_ON_WHILE_PLUGGED_IN, "0") } catch (_: Exception) {}
        val br = p.optInt("brightness", -1)
        if (br in 0..255 && Build.VERSION.SDK_INT >= 28) try {
            d.setSystemSetting(a, Settings.System.SCREEN_BRIGHTNESS_MODE, "0"); d.setSystemSetting(a, Settings.System.SCREEN_BRIGHTNESS, br.toString())
        } catch (e: Exception) { Log.w(TAG, "brightness: $e") }
        if (p.optBoolean("no_screensaver")) try { d.setSecureSetting(a, "screensaver_enabled", "0") } catch (e: Exception) { Log.w(TAG, "screensaver (may be unsupported): $e") }
        if (Build.VERSION.SDK_INT >= 23) try {
            val su = p.optString("sysupdate")
            val pol = when {
                su == "postpone" -> SystemUpdatePolicy.createPostponeInstallPolicy()
                su == "auto" -> SystemUpdatePolicy.createAutomaticInstallPolicy()
                su.startsWith("window:") -> { val x = su.removePrefix("window:").split("-"); SystemUpdatePolicy.createWindowedInstallPolicy(x[0].toInt(), x[1].toInt()) }
                else -> null
            }
            d.setSystemUpdatePolicy(a, pol)
        } catch (e: Exception) { Log.w(TAG, "sysupdate: $e") }
    }

    // ---------------- boot relock: only once remote support is ready ----------------
    @Volatile var pendingRelock = false
    fun bootRelock(c: Context, reboot: Boolean = false) {
        if (!AdolunaDO.isOwner(c) || AdolunaDO.kioskPkg(c) == null) return
        // b82: a reboot always ends a staff temporary unlock
        if (reboot) { prefs(c).edit().remove("unlock_until").apply(); hideFloat(c) }
        val until = prefs(c).getLong("unlock_until", 0)
        if (until > System.currentTimeMillis()) { scheduleRelock(c, until - System.currentTimeMillis()); return }
        pendingRelock = true
        AdolunaDO.ensureService(c, "boot", 0)
        fun tick(i: Int) {
            if (!pendingRelock) return
            if (MainService.isReady) { pendingRelock = false; Log.i(TAG, "boot: remote ready after ${i * 5}s, locking"); AdolunaDO.launchKiosk(c); return }
            if (i % 12 == 11) AdolunaDO.ensureService(c, "boot-wait", 0)
            main.postDelayed({ tick(i + 1) }, 5000)
        }
        tick(0)
    }

    // ---------------- fallback unlock ----------------
    fun verifyCode(c: Context, code: String): Boolean {
        val code = code.trim()
        if (code.length < 4) return false
        val pr = prefs(c)
        val h = pr.getString("uc_h", null); val s = pr.getString("uc_s", "") ?: ""
        if (h != null && sha256(s + code) == h.lowercase()) return true
        val k = pr.getString("otp_k", null) ?: return false
        val used = pr.getStringSet("otp_used", emptySet()) ?: emptySet()
        if (code in used) return false
        val step = System.currentTimeMillis() / 1000 / 600
        for (t in listOf(step, step - 1, step + 1)) if (otp(c, k, t) == code) {
            pr.edit().putStringSet("otp_used", (used + code).toList().takeLast(30).toSet()).apply(); return true
        }
        return false
    }

    fun sha256(s: String) = MessageDigest.getInstance("SHA-256").digest(s.toByteArray()).joinToString("") { "%02x".format(it) }

    /** 8-digit one-time code: HMAC-SHA256(hexSecret, "adoluna-otp-v1:<deviceId>:<10-minute step>"). Backend computes the same. */
    fun otp(c: Context, hexKey: String, step: Long): String {
        val key = hexKey.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
        val m = Mac.getInstance("HmacSHA256"); m.init(SecretKeySpec(key, "HmacSHA256"))
        val id = prefs(c).getString("dev_id", "") ?: ""
        val d = m.doFinal("adoluna-otp-v1:$id:$step".toByteArray())
        val v = ((d[0].toLong() and 0x7f) shl 24) or ((d[1].toLong() and 0xff) shl 16) or ((d[2].toLong() and 0xff) shl 8) or (d[3].toLong() and 0xff)
        return "%08d".format(v % 100_000_000)
    }

    fun unlockFor(c: Context, minutes: Int) {
        val until = System.currentTimeMillis() + minutes * 60_000L
        prefs(c).edit().putLong("unlock_until", until).apply()
        AdolunaDO.stopLockKeepPkg(c)
        scheduleRelock(c, minutes * 60_000L)
        showFloat(c)
        Log.i(TAG, "fallback unlock for $minutes min")
    }

    private fun scheduleRelock(c: Context, ms: Long) {
        main.postDelayed({
            val until = prefs(c).getLong("unlock_until", 0)
            if (until == 0L || AdolunaDO.kioskPkg(c) == null) return@postDelayed
            if (System.currentTimeMillis() + 1000 < until) return@postDelayed
            prefs(c).edit().remove("unlock_until").apply()
            Log.i(TAG, "timed unlock over, relocking")
            hideFloat(c)
            bootRelock(c)
        }, ms)
    }

    // Corner hotspot (top-left, ~56dp): 7 taps within 5 s opens the unlock dialog.
    private var hotspot: View? = null
    private val taps = ArrayDeque<Long>()
    fun showHotspot(c: Context) = main.post {
        if (hotspot != null || !AdolunaDO.isOwner(c) || AdolunaDO.kioskPkg(c) == null) return@post
        if (Build.VERSION.SDK_INT >= 23 && !Settings.canDrawOverlays(c)) { Log.w(TAG, "hotspot: no overlay permission"); return@post }
        val wm = c.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val size = (56 * c.resources.displayMetrics.density).toInt()
        val v = View(c); v.setBackgroundColor(Color.TRANSPARENT)
        v.setOnTouchListener { _, e ->
            if (e.action == MotionEvent.ACTION_DOWN) {
                val now = SystemClock.uptimeMillis(); taps.addLast(now)
                while (taps.isNotEmpty() && now - taps.first() > 5000) taps.removeFirst()
                if (taps.size >= 7) { taps.clear(); openUnlock(c) }
            }
            false
        }
        val type = if (Build.VERSION.SDK_INT >= 26) WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY else @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE
        val lp = WindowManager.LayoutParams(size, size, type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL, PixelFormat.TRANSLUCENT)
        lp.gravity = Gravity.TOP or Gravity.START
        try { wm.addView(v, lp); hotspot = v; Log.i(TAG, "hotspot shown") } catch (e: Exception) { Log.w(TAG, "hotspot: $e") }
    }
    fun hideHotspot(c: Context) = main.post {
        val v = hotspot ?: return@post; hotspot = null
        try { (c.getSystemService(Context.WINDOW_SERVICE) as WindowManager).removeView(v) } catch (_: Exception) {}
    }
    private fun openUnlock(c: Context) {
        try { c.startActivity(Intent(c, AdolunaUnlockActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } catch (e: Exception) { Log.w(TAG, "unlock dialog: $e") }
    }


    // ---------------- b82: staff temporary unlock - state, 「立即锁定」, floating countdown, watchdog ----------------
    fun unlockUntil(c: Context): Long {
        val u = prefs(c).getLong("unlock_until", 0)
        return if (u > System.currentTimeMillis() && AdolunaDO.kioskPkg(c) != null) u else 0L
    }
    fun kioskLabel(c: Context): String {
        val pkg = AdolunaDO.kioskPkg(c) ?: return ""
        return try { c.packageManager.getApplicationLabel(c.packageManager.getApplicationInfo(pkg, 0)).toString() } catch (_: Exception) { pkg }
    }
    /** No PIN needed: locking is always allowed. Relocks to the backend-set app. */
    fun relockNow(c: Context) {
        if (prefs(c).getLong("unlock_until", 0) == 0L) return
        Log.i(TAG, "staff relock now")
        prefs(c).edit().remove("unlock_until").apply()
        hideFloat(c)
        bootRelock(c)
    }

    private var float: TextView? = null
    private val floatTick = object : Runnable {
        override fun run() {
            val v = float ?: return
            val left = (prefs(v.context).getLong("unlock_until", 0) - System.currentTimeMillis()) / 1000
            if (left <= 0) { hideFloat(v.context); return }
            val zh = v.resources.configuration.locale.language == "zh"
            v.text = "%s %02d:%02d · %s".format(if (zh) "剩余" else "Left", left / 60, left % 60, if (zh) "锁定" else "Lock")
            main.postDelayed(this, 1000)
        }
    }
    fun showFloat(c: Context) { main.post {
        val ac = c.applicationContext
        if (float != null || unlockUntil(ac) == 0L) return@post
        if (Build.VERSION.SDK_INT >= 23 && !Settings.canDrawOverlays(ac)) { Log.w(TAG, "float: no overlay permission"); return@post }
        val wm = ac.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val d = ac.resources.displayMetrics.density
        val v = TextView(ac); v.setTextColor(Color.WHITE); v.textSize = 15f
        v.setPadding((14 * d).toInt(), (8 * d).toInt(), (14 * d).toInt(), (8 * d).toInt())
        v.background = android.graphics.drawable.GradientDrawable().apply { setColor(0xEED7262E.toInt()); cornerRadius = 20 * d }
        val type = if (Build.VERSION.SDK_INT >= 26) WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY else @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE
        val lp = WindowManager.LayoutParams(WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT, type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE, PixelFormat.TRANSLUCENT)
        lp.gravity = Gravity.TOP or Gravity.START; lp.x = (8 * d).toInt(); lp.y = (40 * d).toInt()
        var dx = 0f; var dy = 0f; var sx = 0; var sy = 0; var moved = false
        v.setOnTouchListener { _, e ->
            when (e.action) {
                MotionEvent.ACTION_DOWN -> { dx = e.rawX; dy = e.rawY; sx = lp.x; sy = lp.y; moved = false }
                MotionEvent.ACTION_MOVE -> {
                    val mx = e.rawX - dx; val my = e.rawY - dy
                    if (Math.abs(mx) > 12 * d || Math.abs(my) > 12 * d) moved = true
                    if (moved) { lp.x = sx + mx.toInt(); lp.y = sy + my.toInt(); try { wm.updateViewLayout(v, lp) } catch (_: Exception) {} }
                }
                MotionEvent.ACTION_UP -> if (!moved) relockNow(ac)
            }
            true
        }
        try { wm.addView(v, lp); float = v; main.post(floatTick); Log.i(TAG, "float shown") } catch (e: Exception) { Log.w(TAG, "float: $e") }
    } }
    fun hideFloat(c: Context) {
        main.post {
            val v = float ?: return@post; float = null
            main.removeCallbacks(floatTick)
            try { (c.applicationContext.getSystemService(Context.WINDOW_SERVICE) as WindowManager).removeView(v) } catch (_: Exception) {}
        }
    }

    /** POS crashed / task removed while locked: bring the lock back (fallback page + POS as root). */
    private var wdOn = false
    private var wdLast = 0L
    fun startWatchdog(c: Context) {
        if (wdOn) return; wdOn = true
        val ac = c.applicationContext
        lateinit var tick: Runnable
        tick = Runnable {
            try {
                if (AdolunaDO.isOwner(ac) && AdolunaDO.kioskPkg(ac) != null && !pendingRelock && unlockUntil(ac) == 0L &&
                    !AdolunaKioskActivity.permissionInFlight && AdolunaKioskActivity.instance == null &&
                    MainService.isReady && AdolunaDO.lockState(ac) == 0 && SystemClock.uptimeMillis() - wdLast > 15_000) {
                    wdLast = SystemClock.uptimeMillis()
                    Log.i(TAG, "watchdog: lock task not active, relocking")
                    AdolunaDO.launchKiosk(ac)
                }
            } catch (e: Exception) { Log.w(TAG, "watchdog: $e") }
            main.postDelayed(tick, 5000)
        }
        main.postDelayed(tick, 20_000)
    }

    // ---------------- keep-alive (all devices): keep CPU + Wi-Fi awake while the service runs ----------------
    private var wl: PowerManager.WakeLock? = null
    private var wifi: WifiManager.WifiLock? = null
    fun keepAlive(c: Context, on: Boolean) {
        try {
            if (on) {
                if (wl == null) wl = (c.getSystemService(Context.POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "adoluna:keepalive").apply { setReferenceCounted(false); acquire() }
                @Suppress("DEPRECATION")
                if (wifi == null) wifi = (c.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager).createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "adoluna:keepalive").apply { setReferenceCounted(false); acquire() }
            } else { wl?.release(); wl = null; wifi?.release(); wifi = null }
        } catch (e: Exception) { Log.w(TAG, "keepAlive: $e") }
    }
}

/** Fallback unlock dialog (shown over the kiosk app; our package is on the lock-task allowlist). */
class AdolunaUnlockActivity : Activity() {
    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        val zh = resources.configuration.locale.language == "zh"
        val pad = (20 * resources.displayMetrics.density).toInt()
        val l = LinearLayout(this); l.orientation = LinearLayout.VERTICAL; l.setPadding(pad, pad, pad, pad)
        val t = TextView(this); t.text = if (zh) "输入 Adoluna 技术支持提供的解锁码" else "Enter the unlock code from Adoluna Support"; t.textSize = 18f
        val e = EditText(this); e.inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
        val msg = TextView(this)
        val ok = Button(this); ok.text = if (zh) "解锁 30 分钟" else "Unlock for 30 minutes"
        val cancel = Button(this); cancel.text = if (zh) "取消" else "Cancel"
        var fails = 0
        ok.setOnClickListener {
            if (fails >= 5) { msg.text = if (zh) "错误次数过多，请稍后再试" else "Too many attempts"; return@setOnClickListener }
            if (AdolunaDevice.verifyCode(this, e.text.toString())) { AdolunaDevice.unlockFor(this, 30); finish() }
            else { fails++; msg.text = if (zh) "解锁码不正确" else "Wrong code"; e.setText("") }
        }
        cancel.setOnClickListener { finish() }
        l.addView(t); l.addView(e); l.addView(msg); l.addView(ok); l.addView(cancel)
        setContentView(l)
    }
}
