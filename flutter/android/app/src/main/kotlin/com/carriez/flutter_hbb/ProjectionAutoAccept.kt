package com.carriez.flutter_hbb

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * b111: auto-accept the system MediaProjection ("start capturing / recording") consent dialog
 * for OUR app only, using the already-enabled accessibility service.
 *
 * Safety rules:
 *  - only windows owned by the system UI package (com.android.systemui or an OEM *.systemui)
 *  - the dialog text must name our own app label (Adoluna Support) AND look like a capture dialog
 *  - the button we click must be android:id/button1 or carry a known "Start now" label
 *  - never touches notification shade / any other app's dialog
 * Every auto-accept is written to logcat (tag AdolunaAutoAccept), which the backend "logs" command collects.
 */
object ProjectionAutoAccept {
    private const val TAG = "AdolunaAutoAccept"
    private val main = Handler(Looper.getMainLooper())
    private var lastAcceptAt = 0L
    private var lastScanAt = 0L
    private var selectorOpenedAt = 0L
    private var pendingClick = false

    // b111: only auto-accept within this window after OUR app asked for screen capture
    // (the single chokepoint is PermissionRequestTransparentActivity). Avoids ever acting on an
    // unrelated system-UI dialog. Set generously; the dialog + user-absent retries fit inside it.
    private const val ARM_WINDOW_MS = 30_000L
    @Volatile private var captureRequestedAt = 0L
    fun armForRequest() {
        captureRequestedAt = SystemClock.uptimeMillis()
        Log.i(TAG, "screen-capture consent requested by our app; auto-accept armed for ${ARM_WINDOW_MS}ms")
    }
    private fun armed(): Boolean {
        val t = captureRequestedAt
        return t != 0L && SystemClock.uptimeMillis() - t <= ARM_WINDOW_MS
    }

    private val START_LABELS = setOf(
        "start now", "start", "next", "share screen", "start recording", "start casting", "allow",
        "立即开始", "开始", "下一步", "共享屏幕", "开始录制", "允许",
        "立即開始", "開始", "共用螢幕", "分享螢幕", "允許",
        "iniciar ahora", "empezar ahora", "comenzar ahora", "iniciar", "empezar", "comenzar", "siguiente",
        "iniciar agora", "começar agora", "começar", "próximo",
        "démarrer maintenant", "commencer maintenant", "démarrer", "commencer", "suivant",
        "jetzt starten", "starten", "weiter",
        "avvia ora", "inizia ora", "avvia", "inizia", "avanti",
        "今すぐ開始", "開始する", "次へ",
        "지금 시작", "시작", "다음",
        "начать", "начать сейчас", "далее",
        "bắt đầu ngay", "bắt đầu", "tiếp theo",
        "mulai sekarang", "mulai", "berikutnya",
        "เริ่มเลย", "เริ่ม",
        "şimdi başlat", "başlat"
    )
    private val CAPTURE_WORDS = listOf(
        "captur", "record", "cast", "screen", "display", "shar",
        "录", "屏幕", "投", "截", "共享", "錄", "螢幕", "投放", "分享",
        "pantalla", "grab", "écran", "enregistr", "bildschirm", "aufzeichn", "aufnehm",
        "schermo", "registra", "画面", "録画", "キャプチャ", "화면", "녹화", "экран", "запис",
        "tela", "gravar", "màn hình", "layar", "หน้าจอ", "ekran"
    )
    private val ENTIRE_SCREEN_WORDS = listOf(
        "entire screen", "full screen", "whole screen", "整个屏幕", "全屏", "整個螢幕", "pantalla completa",
        "toda la pantalla", "tela inteira", "écran entier", "gesamten bildschirm", "intero schermo",
        "画面全体", "전체 화면", "весь экран"
    )

    private fun isSystemUi(pkg: CharSequence?): Boolean {
        val p = pkg?.toString() ?: return false
        return p == "com.android.systemui" || p.endsWith(".systemui")
    }

    private fun norm(s: CharSequence?): String =
        (s?.toString() ?: "").trim().lowercase().replace('\u00a0', ' ').replace(Regex("\\s+"), " ")

    fun onEvent(svc: AccessibilityService, event: AccessibilityEvent) {
        try {
            if (!armed()) return
            if (!isSystemUi(event.packageName)) return
            val now = SystemClock.uptimeMillis()
            if (now - lastAcceptAt < 3000) return
            val isState = event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
            if (!isState && now - lastScanAt < 250) return
            lastScanAt = now
            scan(svc, "event")
        } catch (t: Throwable) {
            Log.w(TAG, "event failed: ${t.javaClass.simpleName}: ${t.message}")
        }
    }

    private fun candidateRoots(svc: AccessibilityService): List<AccessibilityNodeInfo> {
        val out = ArrayList<AccessibilityNodeInfo>()
        try { svc.rootInActiveWindow?.let { out.add(it) } } catch (_: Throwable) {}
        try {
            for (w in svc.windows) {
                val r = try { w.root } catch (_: Throwable) { null } ?: continue
                out.add(r)
            }
        } catch (_: Throwable) {}
        return out.filter { isSystemUi(it.packageName) }
    }

    private fun walk(root: AccessibilityNodeInfo, f: (AccessibilityNodeInfo) -> Unit) {
        val stack = ArrayDeque<Pair<AccessibilityNodeInfo, Int>>()
        stack.addLast(root to 0)
        var count = 0
        while (stack.isNotEmpty() && count < 600) {
            val (n, d) = stack.removeLast()
            count++
            f(n)
            if (d > 40) continue
            for (i in n.childCount - 1 downTo 0) {
                val c = try { n.getChild(i) } catch (_: Throwable) { null } ?: continue
                stack.addLast(c to d + 1)
            }
        }
    }

    private fun scan(svc: AccessibilityService, why: String) {
        val label = norm(try { svc.packageManager.getApplicationLabel(svc.applicationInfo) } catch (_: Throwable) { "Adoluna Support" })
        if (label.isEmpty()) return
        for (root in candidateRoots(svc)) {
            val texts = StringBuilder()
            var button1: AccessibilityNodeInfo? = null
            val labelled = ArrayList<AccessibilityNodeInfo>()
            var checkbox: AccessibilityNodeInfo? = null
            var selector: AccessibilityNodeInfo? = null
            var entireItem: AccessibilityNodeInfo? = null
            var looksLikeShade = false
            walk(root) { n ->
                val t = norm(n.text); val cd = norm(n.contentDescription)
                if (t.isNotEmpty()) texts.append(t).append('\n')
                if (cd.isNotEmpty()) texts.append(cd).append('\n')
                val id = n.viewIdResourceName ?: ""
                if (id.contains("notification", true) || id.contains("qs_frame", true) || id.contains("quick_settings", true)) looksLikeShade = true
                val cls = n.className?.toString() ?: ""
                if (id == "android:id/button1") button1 = n
                if ((cls.endsWith("Button") || n.isClickable) && (t in START_LABELS || cd in START_LABELS)) labelled.add(n)
                if (n.isCheckable && cls.endsWith("CheckBox")) checkbox = n
                if (cls.endsWith("Spinner")) selector = n
                if (ENTIRE_SCREEN_WORDS.any { t.contains(it) }) entireItem = n
            }
            if (looksLikeShade) continue
            val all = texts.toString()
            if (!all.contains(label)) continue
            if (CAPTURE_WORDS.none { all.contains(it) }) continue
            val sdk = Build.VERSION.SDK_INT

            // Android 14+: if a single-app / entire-screen selector is shown and not on entire screen, open it / pick it.
            selector?.let { sp ->
                val cur = norm(sp.text) + " " + norm(sp.contentDescription) + " " + firstChildText(sp)
                if (ENTIRE_SCREEN_WORDS.none { cur.contains(it) }) {
                    val now = SystemClock.uptimeMillis()
                    if (now - selectorOpenedAt > 1500) {
                        selectorOpenedAt = now
                        val ok = click(svc, sp)
                        Log.i(TAG, "capture dialog: opening screen selector ok=$ok sdk=$sdk")
                        return
                    }
                }
            }
            if (selector == null && entireItem != null && button1 == null && labelled.isEmpty()) {
                // the dropdown list window itself (systemui), pick "entire screen"
                val ok = click(svc, entireItem!!)
                Log.i(TAG, "capture dialog: chose entire screen ok=$ok sdk=$sdk")
                return
            }

            val btn = button1 ?: labelled.firstOrNull() ?: continue
            val btnText = norm(btn.text).ifEmpty { norm(btn.contentDescription) }
            if (btn != button1 && btnText !in START_LABELS) continue
            if (!btn.isEnabled) continue

            var ticked = false
            checkbox?.let { cb ->
                if (!cb.isChecked) { ticked = click(svc, cb) }
            }
            if (ticked && !pendingClick) {
                // let the checkbox state settle, then click Start on a fresh tree
                pendingClick = true
                main.postDelayed({ pendingClick = false; try { scan(svc, "after-checkbox") } catch (_: Throwable) {} }, 250)
                Log.i(TAG, "capture dialog: ticked 'don't show again' sdk=$sdk")
                return
            }
            val ok = click(svc, btn)
            if (ok) { lastAcceptAt = SystemClock.uptimeMillis(); captureRequestedAt = 0L }
            Log.i(TAG, "auto-accepted screen capture consent ok=$ok sdk=$sdk pkg=${root.packageName} button='${btnText.take(40)}' id=${btn.viewIdResourceName} remember=${checkbox?.isChecked} via=$why")
            return
        }
    }

    private fun firstChildText(n: AccessibilityNodeInfo): String {
        val sb = StringBuilder()
        for (i in 0 until minOf(n.childCount, 4)) {
            val c = try { n.getChild(i) } catch (_: Throwable) { null } ?: continue
            sb.append(norm(c.text)).append(' ')
        }
        return sb.toString()
    }

    private fun click(svc: AccessibilityService, node: AccessibilityNodeInfo): Boolean {
        var n: AccessibilityNodeInfo? = node
        var hops = 0
        while (n != null && !n.isClickable && hops < 4) { n = n.parent; hops++ }
        val target = n ?: node
        if (try { target.performAction(AccessibilityNodeInfo.ACTION_CLICK) } catch (_: Throwable) { false }) return true
        // fallback: tap the centre of the node with a gesture
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false
        val r = Rect(); target.getBoundsInScreen(r)
        if (r.isEmpty) return false
        return try {
            val p = Path(); p.moveTo(r.exactCenterX(), r.exactCenterY())
            svc.dispatchGesture(GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(p, 0, 60)).build(), null, null)
        } catch (_: Throwable) { false }
    }
}
