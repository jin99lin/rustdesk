//! Adoluna b79: backend -> device command channel (rides on the existing API heartbeat).
//!
//! The sidecar adds `adoluna_cmd: [{n, k, p, e, sig}]` to heartbeat responses. Each command is
//! signed with the same ed25519 key as passwordless tickets over
//! `"adoluna-cmd-v1\n{id}\n{n}\n{k}\n{p}\n{e}"` (id = this device's ID, n = monotonically
//! increasing command number, k = kind, p = payload JSON string, e = unix expiry).
//! A command is executed only if the signature verifies, it targets this device, it is not
//! expired and n is greater than the last executed number (no replay).  Results are reported
//! back in the next heartbeat as `adoluna_ack: [{n, ok, msg}]`, together with `adoluna_dev`
//! (device facts: OS, model, serial, battery, storage, Device Owner / kiosk state ...).
use hbb_common::{config::Config, log, ResultType};
use serde_json::{json, Value};
use std::sync::Mutex;

const OPT_LAST_N: &str = "adoluna-cmd-n";

lazy_static::lazy_static! {
    static ref ACKS: Mutex<Vec<Value>> = Mutex::new(Vec::new());
    static ref RUNNING: Mutex<()> = Mutex::new(());
    static ref APPS: Mutex<Option<Value>> = Mutex::new(None);
}

/// b81: app list produced by a `list_apps` command, sent once with the next heartbeat (`adoluna_apps`).
pub fn take_apps() -> Option<Value> {
    #[cfg(target_os = "android")]
    if let Ok(s) = scrap::android::call_main_service_get_by_name("adoluna_apps") {
        if let Ok(v) = serde_json::from_str::<Value>(&s) {
            if v.is_object() {
                return Some(v);
            }
        }
    }
    APPS.lock().unwrap().take()
}

/// b85: diagnostics produced by a `logs` command (Android), sent once with the next heartbeat (`adoluna_logs`).
pub fn take_logs() -> Option<Value> {
    #[cfg(target_os = "android")]
    if let Ok(s) = scrap::android::call_main_service_get_by_name("adoluna_logs") {
        if let Ok(v) = serde_json::from_str::<Value>(&s) {
            if v.is_object() {
                return Some(v);
            }
        }
    }
    None
}

/// Put an app list back if the heartbeat carrying it failed.
pub fn restore_apps(v: Value) {
    let mut g = APPS.lock().unwrap();
    if g.is_none() {
        *g = Some(v);
    }
}

pub fn cmd_msg(id: &str, n: i64, k: &str, p: &str, e: i64) -> String {
    format!("adoluna-cmd-v1\n{id}\n{n}\n{k}\n{p}\n{e}")
}

#[derive(Debug, Clone, PartialEq)]
pub struct Cmd {
    pub n: i64,
    pub k: String,
    pub p: Value,
}

pub fn last_n() -> i64 {
    Config::get_option(OPT_LAST_N).parse().unwrap_or(0)
}

/// Verify one command; Ok(None) = old/duplicate (ignore silently).
pub fn verify_cmd_with(
    pk: &hbb_common::sodiumoxide::crypto::sign::PublicKey,
    v: &Value,
    id: &str,
    last: i64,
    now: i64,
) -> ResultType<Option<Cmd>> {
    let n = v["n"].as_i64().ok_or(hbb_common::anyhow::anyhow!("no n"))?;
    let k = v["k"].as_str().unwrap_or_default();
    let p = v["p"].as_str().unwrap_or("{}");
    let e = v["e"].as_i64().unwrap_or(0);
    let sig = v["sig"].as_str().unwrap_or_default();
    if n <= last {
        return Ok(None);
    }
    if k.is_empty() || id.is_empty() {
        hbb_common::bail!("bad cmd");
    }
    crate::adoluna_auth::verify_sig(pk, &cmd_msg(id, n, k, p, e), sig)?;
    if e <= now {
        hbb_common::bail!("expired");
    }
    Ok(Some(Cmd { n, k: k.to_owned(), p: serde_json::from_str(p).unwrap_or(json!({})) }))
}

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

pub fn ack(n: i64, ok: bool, msg: impl Into<String>) {
    let msg: String = msg.into();
    log::info!("adoluna cmd #{n} ok={ok} {msg}");
    let mut a = ACKS.lock().unwrap();
    a.retain(|x| x["n"].as_i64() != Some(n));
    a.push(json!({"n": n, "ok": ok, "msg": msg.chars().take(400).collect::<String>()}));
    while a.len() > 20 {
        a.remove(0);
    }
}

/// Acks to send with the next heartbeat (kept until a heartbeat succeeds).
pub fn pending_acks() -> Vec<Value> {
    let mut v = ACKS.lock().unwrap().clone();
    #[cfg(target_os = "android")]
    if let Ok(s) = scrap::android::call_main_service_get_by_name("adoluna_acks") {
        if let Ok(Value::Array(a)) = serde_json::from_str::<Value>(&s) {
            v.extend(a);
        }
    }
    v
}

pub fn clear_acks(sent: &[Value]) {
    let mut a = ACKS.lock().unwrap();
    a.retain(|x| !sent.contains(x));
    #[cfg(target_os = "android")]
    {
        let s = serde_json::to_string(sent).unwrap_or_default();
        scrap::android::call_main_service_set_by_name("adoluna_acks_clear", Some(&s), None).ok();
    }
}

/// Called from the heartbeat loop with the `adoluna_cmd` array.
/// Numeric build number (b80 = 80), from build.rs (pubspec `+N`). Updates compare this, never the version string.
pub const BUILD: i64 = parse_build(env!("ADOLUNA_BUILD"));
const fn parse_build(s: &str) -> i64 {
    let b = s.as_bytes();
    let mut i = 0;
    let mut n = 0i64;
    while i < b.len() {
        n = n * 10 + (b[i] - b'0') as i64;
        i += 1;
    }
    n
}
/// "1.5.0 (b80)" for About screens.
pub fn version_label() -> String {
    format!("{} (b{})", crate::VERSION, BUILD)
}
/// Decide whether an `update` command should run. Compares build numbers only.
pub fn update_decision(p: &Value, own_build: i64) -> Result<bool, String> {
    let want = match p["build"].as_i64() {
        Some(b) if b > 0 => b,
        _ => return Err("update needs a numeric build number".into()),
    };
    Ok(want > own_build)
}

pub const OPT_STAFF_PIN: &str = "adoluna-staff-pin";

/// Validate a staff_pin payload {"s": salt hex, "h": 32-byte hex, "i": iterations, "v": version}; empty h clears.
pub fn staff_pin_payload(v: &Value) -> Result<String, String> {
    if !v.is_object() {
        return Err("bad payload".into());
    }
    let s = v["s"].as_str().unwrap_or("");
    let h = v["h"].as_str().unwrap_or("");
    let i = v["i"].as_i64().unwrap_or(0);
    if h.is_empty() {
        return Ok(String::new());
    }
    let hexok = |x: &str| !x.is_empty() && x.len() % 2 == 0 && x.chars().all(|c| c.is_ascii_hexdigit());
    if !hexok(s) || s.len() < 16 || h.len() != 64 || !hexok(h) || !(1000..=1_000_000).contains(&i) {
        return Err("bad staff pin payload".into());
    }
    Ok(json!({"s": s, "h": h.to_lowercase(), "i": i, "v": v["v"].clone()}).to_string())
}

pub fn handle(v: Value) {
    let Value::Array(list) = v else { return };
    let pk = match crate::adoluna_auth::pk() {
        Ok(pk) => pk,
        Err(_) => return,
    };
    let id = Config::get_id();
    let mut cmds = Vec::new();
    for c in list.iter() {
        match verify_cmd_with(&pk, c, &id, last_n(), now()) {
            Ok(Some(c)) => cmds.push(c),
            Ok(None) => {}
            Err(e) => {
                log::warn!("adoluna cmd rejected: {e}");
                if let Some(n) = c["n"].as_i64() {
                    if n > last_n() && e.to_string() == "expired" {
                        // still consume it, so the backend sees it was skipped
                        Config::set_option(OPT_LAST_N.to_owned(), n.to_string());
                        ack(n, false, "expired");
                    }
                }
            }
        }
    }
    cmds.sort_by_key(|c| c.n);
    for c in cmds {
        if c.k == "staff_pin" {
            // b80: staff PIN for the version-tap unlock — only a salted PBKDF2 hash is ever stored
            Config::set_option(OPT_LAST_N.to_owned(), c.n.to_string());
            match staff_pin_payload(&c.p) {
                Ok(v) => {
                    Config::set_option(OPT_STAFF_PIN.to_owned(), v);
                    ack(c.n, true, "staff pin set");
                }
                Err(e) => ack(c.n, false, e),
            }
            continue;
        }
        // consume before executing: a command that reboots/updates must never run twice
        Config::set_option(OPT_LAST_N.to_owned(), c.n.to_string());
        std::thread::spawn(move || {
            let _g = RUNNING.lock().unwrap();
            let n = c.n;
            match execute(&c) {
                Ok(Some(m)) => ack(n, true, m),
                Ok(None) => {} // async (Android) - platform reports itself
                Err(e) => ack(n, false, e.to_string()),
            }
        });
    }
}

#[cfg(target_os = "android")]
fn execute(c: &Cmd) -> ResultType<Option<String>> {
    // Device Owner / kiosk / install / reboot are implemented in Kotlin (AdolunaDO.handleCommand),
    // which records its own ack (read back through rustGetByName("adoluna_acks")).
    let arg = json!({"n": c.n, "k": c.k, "p": c.p, "id": Config::get_id()}).to_string();
    scrap::android::call_main_service_set_by_name("adoluna_cmd", Some(&arg), None)
        .map_err(|e| hbb_common::anyhow::anyhow!("app service not running: {e:?}"))?;
    Ok(None)
}

#[cfg(not(target_os = "android"))]
fn execute(c: &Cmd) -> ResultType<Option<String>> {
    match c.k.as_str() {
        "ping" => Ok(Some("pong".into())),
        #[cfg(windows)]
        "list_apps" => desktop::win_list_apps(),
        #[cfg(windows)]
        "uninstall" => desktop::win_uninstall(c.n, &c.p),
        "reboot" => desktop::reboot(),
        "install" => desktop::install(&c.p),
        "update" => desktop::update(&c.p),
        #[cfg(windows)]
        "kiosk_lock" => desktop::win_kiosk_lock(&c.p),
        #[cfg(windows)]
        "kiosk_unlock" => desktop::win_kiosk_unlock(&c.p),
        #[cfg(windows)]
        "unlock_code" => {
            Config::set_option("adoluna-uc-h".into(), c.p["h"].as_str().unwrap_or_default().into());
            Config::set_option("adoluna-uc-s".into(), c.p["s"].as_str().unwrap_or_default().into());
            Ok(Some("unlock code set".into()))
        }
        #[cfg(windows)]
        "otp_secret" => {
            Config::set_option("adoluna-otp-k".into(), c.p["k"].as_str().unwrap_or_default().into());
            Ok(Some("one-time code secret set".into()))
        }
        // b84: real info handler (Windows/macOS/Linux): refresh and return the full device facts,
        // including touchscreen HID/driver details on Windows. Also pushed by the next heartbeat.
        "info" => Ok(Some(desktop::info_fresh().to_string())),
        k => hbb_common::bail!("unsupported on this platform: {k}"),
    }
}

/// Facts about this device for the backend (`adoluna_dev` in the heartbeat).
pub fn device_info() -> Value {
    #[cfg(target_os = "android")]
    {
        if let Ok(s) = scrap::android::call_main_service_get_by_name("adoluna_dev") {
            if let Ok(v) = serde_json::from_str::<Value>(&s) {
                return v;
            }
        }
        return json!({"platform": "Android"});
    }
    #[cfg(not(target_os = "android"))]
    desktop::info()
}

/// b82: start the Windows kiosk watcher thread (idempotent). No-op elsewhere.
pub fn ensure_kiosk_watcher() {
    #[cfg(windows)]
    desktop::ensure_kiosk_watcher();
}

#[cfg(not(target_os = "android"))]
mod desktop {
    use super::*;
    use sha2::{Digest, Sha256};
    use std::path::PathBuf;

    lazy_static::lazy_static! {
        static ref INFO: Mutex<(Option<std::time::Instant>, Value)> = Mutex::new((None, Value::Null));
    }

    pub fn info_fresh() -> Value {
        INFO.lock().unwrap().0 = None;
        #[cfg(windows)]
        {
            *TOUCH.lock().unwrap() = None;
        }
        info()
    }

    #[cfg(windows)]
    lazy_static::lazy_static! {
        static ref TOUCH: Mutex<Option<Value>> = Mutex::new(None);
    }

    /// b84: touchscreen diagnostics (digitizer caps, touch/pointing HID devices + driver, tablet input
    /// service, whether our input-block hook is active). Computed once per process / per `info` command.
    #[cfg(windows)]
    fn touch_info() -> Value {
        let mut g = TOUCH.lock().unwrap();
        if let Some(v) = g.as_ref() {
            return v.clone();
        }
        let (dig, maxt) = unsafe {
            (
                winapi::um::winuser::GetSystemMetrics(94), // SM_DIGITIZER
                winapi::um::winuser::GetSystemMetrics(95), // SM_MAXIMUMTOUCHES
            )
        };
        let ps = |s: &str| run_ps(s).map(|x| x.trim().to_owned()).unwrap_or_default();
        let devs = ps(r#"$r=@(Get-WmiObject Win32_PnPSignedDriver | ?{ $_.DeviceName -match 'touch|digitizer|pen|tablet|eGalax|ILITEK|Elo|Goodix|Weida|SiS|Atmel|eeti|hid-compliant mouse' -or $_.HardWareID -match 'VID_0EEF|VID_222A|VID_04E7|VID_27C6|VID_2575|VID_0457|VID_03EB|VID_1FD2|VID_0DFC|VID_2386' } | %{ [ordered]@{name=$_.DeviceName;mfr=$_.Manufacturer;provider=$_.DriverProviderName;ver=$_.DriverVersion;date="$($_.DriverDate)".Substring(0,[Math]::Min(8,"$($_.DriverDate)".Length));inf=$_.InfName;hwid=("$($_.HardWareID)" -split ' ')[0];class=$_.DeviceClass} }); ConvertTo-Json -Compress -Depth 3 @($r)"#);
        let pointing = ps(r#"$r=@(Get-WmiObject Win32_PointingDevice | %{ [ordered]@{name=$_.Name;mfr=$_.Manufacturer;pnp=$_.PNPDeviceID;status=$_.Status} }); ConvertTo-Json -Compress -Depth 3 @($r)"#);
        let tab = ps("(Get-Service TabletInputService -EA 0).Status");
        let parse = |s: &str| serde_json::from_str::<Value>(s).unwrap_or(json!(s));
        let v = json!({
            "digitizer": dig,
            "integrated_touch": dig & 0x01 != 0,
            "external_touch": dig & 0x02 != 0,
            "multi_input": dig & 0x40 != 0,
            "ready": dig & 0x80 != 0,
            "max_touches": maxt,
            "devices": parse(&devs),
            "pointing": parse(&pointing),
            "tablet_input_service": tab,
            "block_hook_active": crate::platform::windows::adoluna_block_active(),
        });
        *g = Some(v.clone());
        v
    }

    pub fn info() -> Value {
        let mut g = INFO.lock().unwrap();
        if g.0.map(|t| t.elapsed().as_secs() < 300).unwrap_or(false) {
            return g.1.clone();
        }
        #[allow(unused_imports)]
        let mut sys = hbb_common::sysinfo::System::new();
        sys.refresh_memory();
        let mut v = json!({
            "platform": std::env::consts::OS,
            "os": sys.long_os_version().unwrap_or_default(),
            "host": sys.host_name().unwrap_or_default(),
            "mem_mb": sys.total_memory() / 1024 / 1024,
            "ver": crate::VERSION,
            "build": super::BUILD,
        });
        #[cfg(windows)]
        {
            // Get-WmiObject + registry: works on PowerShell 2 (Win7) through Win11.
            let ps = |s: &str| run_ps(s).map(|x| x.trim().to_owned()).unwrap_or_default();
            v["serial"] = json!(ps("(Get-WmiObject Win32_BIOS).SerialNumber"));
            v["model"] = json!(ps("$c=Get-WmiObject Win32_ComputerSystem; \"$($c.Manufacturer) $($c.Model)\""));
            v["disk_free_gb"] = json!(ps("[math]::Round((Get-WmiObject Win32_LogicalDisk -Filter \"DeviceID='C:'\").FreeSpace/1GB,1)"));
            let r = ps("$k=Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion'; \"$($k.EditionID)|$($k.CurrentBuild)|$($k.UBR)|$($k.DisplayVersion)$(if(-not $k.DisplayVersion){$k.ReleaseId})|$($k.ProductName)\"");
            let f: Vec<&str> = r.split('|').collect();
            let g = |i: usize| f.get(i).map(|x| x.trim()).unwrap_or("").to_owned();
            let w = win_caps(&g(0), g(1).parse().unwrap_or(0));
            v["edition"] = json!(g(4));
            v["win"] = json!({"major": w.major, "edition_id": g(0), "edition": w.edition, "build": g(1), "ubr": g(2), "display_version": g(3)});
            v["cap"] = json!({"install": w.install, "kiosk": w.kiosk, "reason": w.reason});
            let full = w.kiosk == "full" && !ps(&format!("{}; if($o -and $o.Configuration){{'1'}}", AA_GET)).is_empty();
            let basic = !ps("if(Get-Command schtasks -EA 0){ schtasks /query /tn AdolunaKiosk 2>$null | Out-Null; if($LASTEXITCODE -eq 0){'1'} }").is_empty();
            v["kiosk"] = json!(full || basic);
            v["kiosk_mode"] = json!(if full { "full" } else if basic { "basic" } else { "" });
            v["kiosk_capable"] = json!(w.kiosk != "none");
            v["touch"] = touch_info();
            v["apps_hash"] = json!(ps(&format!("{WIN_APPS_PS}\r\n$h=($r|%{{$_.name+'|'+$_.exe}}|Sort-Object) -join \"`n\"; $b=[Text.Encoding]::UTF8.GetBytes($h); (([Security.Cryptography.SHA256]::Create().ComputeHash($b))[0..7]|%{{$_.ToString('x2')}}) -join ''")));
        }
        #[cfg(target_os = "macos")]
        {
            let sh = |s: &str| std::process::Command::new("/bin/sh").args(["-c", s]).output().ok()
                .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_owned()).unwrap_or_default();
            v["serial"] = json!(sh("ioreg -c IOPlatformExpertDevice -d 2 | awk -F\\\" '/IOPlatformSerialNumber/{print $4}'"));
            v["model"] = json!(sh("sysctl -n hw.model"));
            v["disk_free_gb"] = json!(sh("df -g / | awk 'NR==2{print $4}'"));
            // b84: real macOS info
            v["os_build"] = json!(sh("sw_vers -buildVersion"));
            v["os_version"] = json!(sh("sw_vers -productVersion"));
            v["cpu"] = json!(sh("sysctl -n machdep.cpu.brand_string"));
            v["arch"] = json!(sh("uname -m"));
            v["boot_time"] = json!(sh("sysctl -n kern.boottime"));
            v["mem_pressure"] = json!(sh("memory_pressure -Q 2>/dev/null | tail -1"));
        }
        #[cfg(target_os = "linux")]
        {
            v["model"] = json!(std::fs::read_to_string("/sys/class/dmi/id/product_name").unwrap_or_default().trim());
        }
        *g = (Some(std::time::Instant::now()), v.clone());
        v
    }

    fn dl_dir() -> PathBuf {
        #[cfg(windows)]
        let d = PathBuf::from(std::env::var("ProgramData").unwrap_or("C:\\ProgramData".into()))
            .join("AdolunaSupport").join("dl");
        #[cfg(not(windows))]
        let d = PathBuf::from("/tmp/.adoluna-dl");
        std::fs::create_dir_all(&d).ok();
        d
    }

    /// Download over HTTPS (or the configured API server) and verify sha256 before anything runs.
    pub fn download(p: &Value) -> ResultType<PathBuf> {
        let url = p["url"].as_str().unwrap_or_default();
        let want = p["sha256"].as_str().unwrap_or_default().to_lowercase();
        if url.is_empty() || want.len() != 64 {
            hbb_common::bail!("url and sha256 are required");
        }
        let name = p["name"].as_str().map(|s| s.to_owned()).unwrap_or_else(|| {
            url.split('?').next().unwrap_or("").rsplit('/').next().unwrap_or("pkg.bin").to_owned()
        });
        let name: String = name.chars().filter(|c| c.is_ascii_alphanumeric() || "._-".contains(*c)).collect();
        if name.is_empty() {
            hbb_common::bail!("bad file name");
        }
        let path = dl_dir().join(&name);
        let client = crate::hbbs_http::create_http_client_with_url(url);
        let mut r = client.get(url).send()?;
        if !r.status().is_success() {
            hbb_common::bail!("download failed: {}", r.status());
        }
        let mut f = std::fs::File::create(&path)?;
        std::io::copy(&mut r, &mut f)?;
        drop(f);
        let got = format!("{:x}", Sha256::digest(std::fs::read(&path)?));
        if got != want {
            std::fs::remove_file(&path).ok();
            hbb_common::bail!("sha256 mismatch");
        }
        Ok(path)
    }

    pub fn reboot() -> ResultType<Option<String>> {
        std::thread::spawn(|| {
            std::thread::sleep(std::time::Duration::from_secs(5));
            #[cfg(windows)]
            let _ = std::process::Command::new("shutdown").args(["/r", "/t", "0", "/f"]).status();
            #[cfg(not(windows))]
            let _ = std::process::Command::new("/sbin/shutdown").args(["-r", "now"]).status();
        });
        Ok(Some("rebooting in 5s".into()))
    }

    pub fn install(p: &Value) -> ResultType<Option<String>> {
        #[cfg(windows)]
        {
            let c = caps_now();
            if !c.install {
                hbb_common::bail!("push-install not supported on Windows {} ({})", c.major, c.reason);
            }
        }
        #[cfg(windows)]
        {
            let path = download(p)?;
            let ps = path.to_string_lossy().to_string();
            let args = p["args"].as_str().unwrap_or("");
            let mut cmd = if ps.to_lowercase().ends_with(".msi") {
                let mut c = std::process::Command::new("msiexec");
                c.args(["/i", &ps, "/qn", "/norestart"]);
                for a in args.split_whitespace() { c.arg(a); }
                c
            } else if ps.to_lowercase().ends_with(".exe") {
                let mut c = std::process::Command::new(&ps);
                let a = if args.is_empty() { "/S" } else { args };
                for a in a.split_whitespace() { c.arg(a); }
                c
            } else {
                hbb_common::bail!("only .msi / .exe are supported");
            };
            let st = cmd.status()?;
            std::fs::remove_file(&path).ok();
            let code = st.code().unwrap_or(-1);
            // 3010 = success, reboot required
            if code == 0 || code == 3010 {
                return Ok(Some(format!("installed (exit {code})")));
            }
            hbb_common::bail!("installer exit code {code}");
        }
        #[cfg(not(windows))]
        {
            let _ = p;
            hbb_common::bail!("install is only supported on Windows and Android");
        }
    }

    /// Self-update to a backend-provided build (url + sha256 + ver).
    pub fn update(p: &Value) -> ResultType<Option<String>> {
        match super::update_decision(p, super::BUILD) {
            Err(e) => hbb_common::bail!("{e}"),
            Ok(false) => return Ok(Some(format!("already up to date (b{})", super::BUILD))),
            Ok(true) => {}
        }
        let ver = format!("b{}", p["build"].as_i64().unwrap_or_default());
        if !crate::updater::has_no_active_conns() && !p["force"].as_bool().unwrap_or(false) {
            hbb_common::bail!("busy: remote session active, retry later");
        }
        #[cfg(windows)]
        {
            let path = download(p)?;
            let ps = path.to_string_lossy().to_string();
            if !ps.to_lowercase().ends_with(".msi") {
                hbb_common::bail!("Windows update needs the .msi package");
            }
            // msiexec stops/restarts our service; run it detached so it outlives us.
            use std::os::windows::process::CommandExt;
            std::process::Command::new("msiexec")
                .args(["/i", &ps, "/qn", "LAUNCH_TRAY_APP=N", "REBOOT=ReallySuppress", "/norestart"])
                .creation_flags(0x00000008 | 0x00000200) // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                .spawn()?;
            return Ok(Some(format!("updating to {ver}")));
        }
        #[cfg(target_os = "macos")]
        {
            // The heartbeat runs in the user-level server; the root LaunchDaemon performs the
            // install.  Hand over the signed command; root re-verifies it (see mac_root_poll).
            let dir = std::path::Path::new("/Users/Shared/AdolunaSupport");
            std::fs::create_dir_all(dir)?;
            std::fs::write(dir.join("update.json"), p.to_string())?;
            return Ok(Some(format!("update to {ver} queued for the system service")));
        }
        #[cfg(target_os = "linux")]
        {
            let path = download(p)?;
            let st = std::process::Command::new("apt-get")
                .args(["install", "-y", "--allow-downgrades", &path.to_string_lossy()])
                .status()?;
            if st.success() { return Ok(Some(format!("updated to {ver}"))); }
            hbb_common::bail!("apt-get failed: {:?}", st.code());
        }
    }

    // ---------------- Windows kiosk (Assigned Access, multi-app profile with one auto-launched app) ----------
    #[cfg(windows)]
    const AA_GET: &str = "$o=Get-CimInstance -Namespace root\\cimv2\\mdm\\dmmap -ClassName MDM_AssignedAccess";

    #[cfg(windows)]
    pub fn run_ps(script: &str) -> ResultType<String> {
        use std::os::windows::process::CommandExt;
        static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let f = dl_dir().join(format!("adl-{}-{}.ps1", std::process::id(), SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed)));
        std::fs::write(&f, format!("$ErrorActionPreference='Stop'\r\n{script}\r\n"))?;
        let o = std::process::Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(&f)
            .creation_flags(0x08000000) // CREATE_NO_WINDOW
            .output()?;
        std::fs::remove_file(&f).ok();
        if !o.status.success() {
            hbb_common::bail!("{}", String::from_utf8_lossy(&o.stderr).lines().take(3).collect::<Vec<_>>().join(" "));
        }
        Ok(String::from_utf8_lossy(&o.stdout).to_string())
    }

    fn xml_esc(s: &str) -> String {
        s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;").replace('\'', "&apos;")
    }

    /// Assigned Access XML: one auto-launched app + our own exe allowed (so remote support keeps
    /// working inside the kiosk session), dedicated auto-logon kiosk account created by Windows.
    pub fn kiosk_xml(app: &str, own_exe: &str, name: &str) -> String {
        let (app, own, name) = (xml_esc(app), xml_esc(own_exe), xml_esc(name));
        format!(r#"<?xml version="1.0" encoding="utf-8"?>
<AssignedAccessConfiguration xmlns="http://schemas.microsoft.com/AssignedAccess/2017/config" xmlns:rs5="http://schemas.microsoft.com/AssignedAccess/201810/config">
  <Profiles>
    <Profile Id="{{6A1D6E5C-6F0B-4D2A-9C1B-AD0F00B79001}}">
      <AllAppsList>
        <AllowedApps>
          <App DesktopAppPath="{app}" rs5:AutoLaunch="true" />
          <App DesktopAppPath="{own}" />
        </AllowedApps>
      </AllAppsList>
      <StartLayout><![CDATA[<LayoutModificationTemplate xmlns:defaultlayout="http://schemas.microsoft.com/Start/2014/FullDefaultLayout" xmlns:start="http://schemas.microsoft.com/Start/2014/StartLayout" Version="1" xmlns="http://schemas.microsoft.com/Start/2014/LayoutModification"><LayoutOptions StartTileGroupCellWidth="6" /><DefaultLayoutOverride><StartLayoutCollection><defaultlayout:StartLayout GroupCellWidth="6" /></StartLayoutCollection></DefaultLayoutOverride></LayoutModificationTemplate>]]></StartLayout>
      <Taskbar ShowTaskbar="false" />
    </Profile>
  </Profiles>
  <Configs>
    <Config>
      <AutoLogonAccount rs5:DisplayName="{name}" />
      <DefaultProfile Id="{{6A1D6E5C-6F0B-4D2A-9C1B-AD0F00B79001}}" />
    </Config>
  </Configs>
</AssignedAccessConfiguration>"#)
    }

    #[derive(Debug, PartialEq)]
    pub struct WinCaps {
        pub major: &'static str,
        pub edition: &'static str,
        pub install: bool,
        /// "full" = Assigned Access (Win11 Pro+), "basic" = our fallback lock (Win10 Pro+), "none"
        pub kiosk: &'static str,
        pub reason: &'static str,
    }

    /// Jay 10/31: Win11 Pro+ full kiosk; Win10 Pro+ basic lock; Home no kiosk (push-install ok);
    /// Win7/8 unsupported (buttons greyed out in the admin).
    pub fn win_caps(edition_id: &str, build: u32) -> WinCaps {
        let major = match build {
            b if b >= 22000 => "11",
            b if b >= 10240 => "10",
            b if b >= 9600 => "8.1",
            b if b >= 9200 => "8",
            b if b >= 7600 => "7",
            _ => "?",
        };
        let e = edition_id.to_lowercase();
        let edition = if e.starts_with("core") || e.contains("home") {
            "Home"
        } else if e.starts_with("professional") {
            "Pro"
        } else if e.starts_with("enterprise") || e.starts_with("iot") {
            "Enterprise"
        } else if e.starts_with("education") {
            "Education"
        } else if e.is_empty() {
            "?"
        } else {
            "Other"
        };
        let home = edition == "Home";
        if major != "11" && major != "10" {
            return WinCaps { major, edition, install: false, kiosk: "none", reason: "os_unsupported" };
        }
        let kiosk = if home { "none" } else if major == "11" { "full" } else { "basic" };
        WinCaps { major, edition, install: true, kiosk, reason: if home { "needs_pro" } else { "" } }
    }

    #[cfg(windows)]
    fn caps_now() -> WinCaps {
        let r = run_ps("$k=Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion'; \"$($k.EditionID)|$($k.CurrentBuild)\"").unwrap_or_default();
        let mut f = r.trim().split('|');
        let e = f.next().unwrap_or("").to_owned();
        win_caps(&e, f.next().unwrap_or("0").trim().parse().unwrap_or(0))
    }

    /// Basic lock for Win10 Pro (no Assigned Access): a logon task in the user's session keeps the
    /// POS app running (relaunch on exit) and hides the taskbar; machine policies block Task Manager,
    /// Win-key hotkeys, lock/switch user/sign-out.  Remote support is untouched (it is a service).
    #[cfg(windows)]
    const BASIC_POL: &[(&str, &str, u32)] = &[
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "DisableTaskMgr", 1),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "DisableLockWorkstation", 1),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "DisableChangePassword", 1),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "HideFastUserSwitching", 1),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer", "NoWinKeys", 1),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer", "NoLogoff", 1),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer", "NoTrayContextMenu", 1),
        // Ctrl+Alt+Del screen mitigation: Task Manager / Lock / Switch user / Change password / Sign out already off above.
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer", "NoRun", 1),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer", "NoControlPanel", 1),
        ("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\System", "DisableCMD", 1),
        // b82: block the left/right edge swipe (Action Center / Task View) on touch POS screens
        ("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\EdgeUI", "AllowEdgeSwipe", 0),
    ];

    #[cfg(windows)]
    pub(super) fn kiosk_dir() -> PathBuf {
        dl_dir().parent().map(|d| d.to_path_buf()).unwrap_or_else(dl_dir)
    }

    // b82: both Win keys off at the keyboard driver (Scancode Map). Any foreign map is saved under
    // HKLM\SOFTWARE\AdolunaSupport\ScancodeMapBackup and restored on unlock.  Needs a reboot to apply.
    #[cfg(windows)]
    const SCANCODE_ON_PS: &str = "$k='HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Keyboard Layout'; $b='HKLM:\\SOFTWARE\\AdolunaSupport'\r\n\
$ours=[byte[]](0,0,0,0,0,0,0,0,3,0,0,0,0,0,0x5B,0xE0,0,0,0x5C,0xE0,0,0,0,0)\r\n\
if(-not (Test-Path $b)){New-Item -Path $b -Force | Out-Null}\r\n\
$cur=(Get-ItemProperty -Path $k -Name 'Scancode Map' -EA SilentlyContinue).'Scancode Map'\r\n\
$bk=(Get-ItemProperty -Path $b -Name 'ScancodeMapBackup' -EA SilentlyContinue).'ScancodeMapBackup'\r\n\
if($null -eq $bk){ if($null -eq $cur){ New-ItemProperty -Path $b -Name 'ScancodeMapBackup' -Value ([byte[]]@()) -PropertyType Binary -Force | Out-Null } else { New-ItemProperty -Path $b -Name 'ScancodeMapBackup' -Value $cur -PropertyType Binary -Force | Out-Null } }\r\n\
New-ItemProperty -Path $k -Name 'Scancode Map' -Value $ours -PropertyType Binary -Force | Out-Null";

    #[cfg(windows)]
    const SCANCODE_OFF_PS: &str = "$k='HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Keyboard Layout'; $b='HKLM:\\SOFTWARE\\AdolunaSupport'\r\n\
$bk=(Get-ItemProperty -Path $b -Name 'ScancodeMapBackup' -EA SilentlyContinue).'ScancodeMapBackup'\r\n\
if($null -ne $bk){ if($bk.Length -eq 0){ Remove-ItemProperty -Path $k -Name 'Scancode Map' -EA SilentlyContinue } else { New-ItemProperty -Path $k -Name 'Scancode Map' -Value $bk -PropertyType Binary -Force | Out-Null }; Remove-ItemProperty -Path $b -Name 'ScancodeMapBackup' -EA SilentlyContinue }\r\n\
else { Remove-ItemProperty -Path $k -Name 'Scancode Map' -EA SilentlyContinue }";

    #[cfg(windows)]
    fn basic_lock(app: &str) -> ResultType<Option<String>> {
        let dir = kiosk_dir();
        std::fs::create_dir_all(&dir).ok();
        let ps1 = dir.join("kiosk.ps1");
        let own = std::env::current_exe()?.file_stem().map(|x| x.to_string_lossy().to_string()).unwrap_or_default();
        let name = std::path::Path::new(app).file_stem().map(|x| x.to_string_lossy().to_string()).unwrap_or_default();
        let script = include_str!("adoluna_kiosk.ps1")
            .replace("{{APP}}", &app.replace('\'', "''"))
            .replace("{{NAME}}", &name.replace('\'', "''"))
            .replace("{{OWN}}", &own.replace('\'', "''"));
        // UTF-8 BOM so PowerShell reads the Chinese strings correctly.
        let mut bytes = vec![0xEF, 0xBB, 0xBF];
        bytes.extend_from_slice(script.as_bytes());
        std::fs::write(&ps1, bytes)?;
        // user-writable request dir (the kiosk loop runs as the limited user and drops files here)
        let reqdir = dir.join("kiosk-req");
        std::fs::create_dir_all(&reqdir).ok();
        let mut sc = String::new();
        sc += &SCANCODE_ON_PS.replace("\r\n", "\r\n");
        sc += "\r\n";
        for (k, n, val) in BASIC_POL {
            sc += &format!("if(-not (Test-Path '{k}')){{New-Item -Path '{k}' -Force|Out-Null}}; Set-ItemProperty -Path '{k}' -Name {n} -Value {val} -Type DWord\r\n");
        }
        // grant the limited users group write/modify on the request dir, read on the rest
        sc += &format!("icacls '{}' /grant '*S-1-5-32-545:(OI)(CI)M' /T | Out-Null\r\n", reqdir.to_string_lossy());
        sc += &format!("$ErrorActionPreference='Continue'\r\nschtasks /delete /tn AdolunaKiosk /f 2>$null | Out-Null\r\nschtasks /create /tn AdolunaKiosk /sc onlogon /ru BUILTIN\\Users /rl LIMITED /f /tr 'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"{}\"' | Out-Null\r\nif($LASTEXITCODE -ne 0){{throw 'schtasks create failed'}}\r\nschtasks /run /tn AdolunaKiosk 2>$null | Out-Null", ps1.to_string_lossy());
        run_ps(&sc)?;
        // clear any stale pause/relock so the loop starts locked
        std::fs::remove_file(dir.join("kiosk-pause")).ok();
        std::fs::remove_file(reqdir.join("relock")).ok();
        ensure_kiosk_watcher();
        Ok(Some("basic lock on (relaunch + hidden taskbar + hotkeys/edge/Win keys blocked); Win keys fully off after next sign-in".into()))
    }

    #[cfg(windows)]
    fn basic_unlock() -> ResultType<()> {
        let dir = kiosk_dir();
        let mut sc = String::from("$ErrorActionPreference='Continue'\r\nschtasks /end /tn AdolunaKiosk 2>$null | Out-Null\r\nschtasks /delete /tn AdolunaKiosk /f 2>$null | Out-Null\r\n");
        for (k, n, _v) in BASIC_POL {
            sc += &format!("Remove-ItemProperty -Path '{k}' -Name {n} -ErrorAction SilentlyContinue\r\n");
        }
        sc += SCANCODE_OFF_PS;
        sc += "\r\n";
        // bring the taskbar back and stop any running loop in the user session
        sc += "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -like '*AdolunaSupport\\kiosk.ps1*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }\r\n";
        run_ps(&sc).map(|_| ())?;
        // drop kiosk state files; clears the pause
        std::fs::remove_file(dir.join("kiosk-pause")).ok();
        std::fs::remove_file(dir.join("kiosk-result")).ok();
        std::fs::remove_file(dir.join("kiosk-req").join("unlock")).ok();
        std::fs::remove_file(dir.join("kiosk-req").join("relock")).ok();
        Config::set_option("adoluna-uc-fails".into(), "".into());
        Ok(())
    }

    /// b81: Start menu apps for the backend's kiosk picker: name, AUMID (Get-StartApps, Win10+) and the
    /// exe a shortcut points to (for the Win10 basic lock / desktop-app Assigned Access).
    #[cfg(windows)]
    pub const WIN_APPS_PS: &str = r#"$ErrorActionPreference='SilentlyContinue'
$sh=New-Object -ComObject WScript.Shell
$lnk=@{}
foreach($d in @("$env:ProgramData\Microsoft\Windows\Start Menu\Programs","$env:PUBLIC\Desktop")){ Get-ChildItem -Path $d -Recurse -Filter *.lnk | %{ $t=$sh.CreateShortcut($_.FullName).TargetPath; if($t -and $t.ToLower().EndsWith('.exe') -and (Test-Path $t)){ $lnk[$_.BaseName]=$t } } }
$r=@(); $seen=@{}
if(Get-Command Get-StartApps){ foreach($a in Get-StartApps){ $e=$lnk[$a.Name]; if(-not $e -and $a.AppID -like '*.exe'){ if(Test-Path $a.AppID){$e=$a.AppID} }
  if($a.AppID -match '^(Microsoft\.Windows\.|windows\.immersivecontrolpanel)'){continue}
  $r+=[pscustomobject]@{name=$a.Name;aumid=$a.AppID;exe=$e}; $seen[$a.Name]=1 } }
foreach($k in $lnk.Keys){ if(-not $seen[$k]){ $r+=[pscustomobject]@{name=$k;aumid='';exe=$lnk[$k]} } }
$r=$r | ?{ $_.exe -notmatch '\\(uninst|unins000|setup)[^\\]*\.exe$' -and $_.name -notmatch 'Adoluna Support' }"#;

    /// b93: one row per Uninstall key. hive = HKLM64 | HKLM32 | HKU:<SID>; code = key name.
    #[cfg(windows)]
    pub const WIN_INV_PS: &str = r#"$ErrorActionPreference='SilentlyContinue'
$U='\Microsoft\Windows\CurrentVersion\Uninstall'
$src=@(@('HKLM64',"Registry::HKEY_LOCAL_MACHINE\SOFTWARE$U"),@('HKLM32',"Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node$U"))
Get-ChildItem 'Registry::HKEY_USERS' | ?{ $_.PSChildName -match '^S-1-5-21-[0-9-]+$' } | %{ $src+=,@("HKU:$($_.PSChildName)","Registry::HKEY_USERS\$($_.PSChildName)\SOFTWARE$U") }
$o=foreach($s in $src){ Get-ChildItem $s[1] | %{ $p=Get-ItemProperty $_.PSPath
 if(-not $p.DisplayName -or $p.ParentKeyName -or $p.ReleaseType -match 'Update|Hotfix'){return}
 $c=$_.PSChildName; $msi=($p.WindowsInstaller -eq 1 -and $c -match '^\{[0-9A-Fa-f-]{36}\}$')
 $h=[ordered]@{name=[string]$p.DisplayName;code=$c;hive=$s[0];pub=[string]$p.Publisher;ver=[string]$p.DisplayVersion;dt=[string]$p.InstallDate}
 if($p.EstimatedSize){$h.size=[int64]$p.EstimatedSize*1024}
 if($p.SystemComponent -eq 1){$h.sys=$true}
 if($p.QuietUninstallString -or $msi){$h.quiet=$true}
 if($msi){$h.msi=$true}
 [pscustomobject]$h } }
@($o | Select-Object -First 700) | ConvertTo-Json -Compress"#;

    /// b93: uninstall one program the backend picked from our own inventory. The payload only carries
    /// {code, hive, name, msi}; the command line is re-read from this PC's registry, never sent.
    #[cfg(windows)]
    pub fn win_uninstall(n: i64, p: &Value) -> ResultType<Option<String>> {
        let code = p["code"].as_str().unwrap_or("").to_string();
        let hive = p["hive"].as_str().unwrap_or("").to_string();
        let name = p["name"].as_str().unwrap_or("").to_string();
        if code.is_empty() || code.len() > 200 || code.chars().any(|ch| ch == '\\' || ch == '/' || ch.is_control()) {
            hbb_common::bail!("bad program id");
        }
        let root = match hive.as_str() {
            "HKLM64" => "HKEY_LOCAL_MACHINE\\SOFTWARE".to_string(),
            "HKLM32" => "HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node".to_string(),
            h if h.starts_with("HKU:S-1-5-21-") && h[4..].chars().all(|ch| ch.is_ascii_digit() || ch == '-' || ch == 'S') =>
                format!("HKEY_USERS\\{}\\SOFTWARE", &h[4..]),
            _ => hbb_common::bail!("bad hive"),
        };
        let q = |s: &str| s.replace('\'', "''");
        let key = format!("Registry::{root}\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{}", q(&code));
        let msi_ok = p["msi"].as_bool().unwrap_or(false) && code.len() == 38 && code.starts_with('{') && code.ends_with('}')
            && code[1..37].chars().all(|ch| ch.is_ascii_hexdigit() || ch == '-');
        let probe = run_ps(&format!("[Console]::OutputEncoding=[Text.Encoding]::UTF8\r\n$p=Get-ItemProperty -LiteralPath '{key}'\r\n@{{name=[string]$p.DisplayName;quiet=[string]$p.QuietUninstallString}} | ConvertTo-Json -Compress"))
            .map_err(|_| hbb_common::anyhow::anyhow!("not found (already removed?)"))?;
        let v: Value = serde_json::from_str(probe.trim().trim_start_matches('\u{feff}')).unwrap_or(json!({}));
        let have = v["name"].as_str().unwrap_or("").to_string();
        if have.is_empty() { hbb_common::bail!("not found (already removed?)"); }
        if !name.is_empty() && have != name { hbb_common::bail!("program changed on this PC ({have}); refresh the list"); }
        let quiet = v["quiet"].as_str().unwrap_or("").trim().to_string();
        if !msi_ok && quiet.is_empty() { hbb_common::bail!("no silent uninstaller; remove it manually"); }
        let label = have.clone();
        std::thread::spawn(move || {
            use std::os::windows::process::CommandExt;
            let st = if msi_ok {
                std::process::Command::new("msiexec").args(["/x", &code, "/qn", "/norestart"]).creation_flags(0x08000000).status()
            } else {
                // QuietUninstallString is a full command line written by the vendor's installer
                std::process::Command::new("cmd.exe").raw_arg(format!("/d /s /c \"{quiet}\"")).creation_flags(0x08000000).status()
            };
            let rc = st.as_ref().ok().and_then(|s| s.code()).unwrap_or(-1);
            // give EXE uninstallers that hand off to a child process time to finish
            let mut gone = false;
            for _ in 0..40 {
                gone = run_ps(&format!("if(Test-Path -LiteralPath '{key}'){{'y'}}else{{'n'}}")).map(|s| s.trim() == "n").unwrap_or(false);
                if gone || st.is_err() { break; }
                std::thread::sleep(std::time::Duration::from_secs(3));
            }
            if gone { super::ack(n, true, format!("uninstalled {label}")); }
            else if let Err(e) = st { super::ack(n, false, format!("uninstall failed: {e}")); }
            else { super::ack(n, false, format!("uninstall failed (exit {rc})")); }
            let _ = win_list_apps();
        });
        Ok(None)
    }

    #[cfg(windows)]
    pub fn win_list_apps() -> ResultType<Option<String>> {
        let out = run_ps(&format!("{WIN_APPS_PS}\r\n[Console]::OutputEncoding=[Text.Encoding]::UTF8\r\n$r | Select-Object -First 300 | ConvertTo-Json -Compress"))?;
        let v: Value = serde_json::from_str(out.trim().trim_start_matches('\u{feff}')).unwrap_or(json!([]));
        let list = match v { Value::Array(a) => a, Value::Object(_) => vec![v], _ => vec![] };
        let apps: Vec<Value> = list.into_iter().map(|a| json!({
            "name": a["name"].as_str().unwrap_or("").chars().take(80).collect::<String>(),
            "aumid": a["aumid"].as_str().unwrap_or(""),
            "exe": a["exe"].as_str().unwrap_or(""),
        })).collect();
        let n = apps.len();
        // installed programs (Add/Remove Programs) with versions, for "already up to date" checks
        let reg = run_ps("[Console]::OutputEncoding=[Text.Encoding]::UTF8\r\n$ErrorActionPreference='SilentlyContinue'\r\nGet-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*','HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' | ?{ $_.DisplayName } | Select-Object -First 400 @{n='name';e={$_.DisplayName}},@{n='ver';e={[string]$_.DisplayVersion}},@{n='code';e={$_.PSChildName}} | ConvertTo-Json -Compress").unwrap_or_default();
        let installed = match serde_json::from_str::<Value>(reg.trim().trim_start_matches('\u{feff}')).unwrap_or(json!([])) {
            Value::Array(a) => a, v @ Value::Object(_) => vec![v], _ => vec![],
        };
        INFO.lock().unwrap().0 = None;
        let hash = info()["apps_hash"].clone();
        // b93: full Add/Remove Programs inventory (HKLM 64/32-bit + every loaded user hive) for the 已装应用 card
        let inv = run_ps(&format!("[Console]::OutputEncoding=[Text.Encoding]::UTF8\r\n{WIN_INV_PS}")).unwrap_or_default();
        let inv = match serde_json::from_str::<Value>(inv.trim().trim_start_matches('\u{feff}')).unwrap_or(json!([])) {
            Value::Array(a) => a, v @ Value::Object(_) => vec![v], _ => vec![],
        };
        *super::APPS.lock().unwrap() = Some(json!({"hash": hash, "apps": apps, "installed": installed, "inv": inv}));
        Ok(Some(format!("apps: {n}")))
    }

    #[cfg(windows)]
    pub fn win_kiosk_lock(p: &Value) -> ResultType<Option<String>> {
        let app = p["app"].as_str().unwrap_or_default();
        if app.is_empty() || !app.to_lowercase().ends_with(".exe") {
            hbb_common::bail!("app must be the full path of an .exe");
        }
        if !std::path::Path::new(app).exists() {
            hbb_common::bail!("app not found: {app}");
        }
        let c = caps_now();
        match c.kiosk {
            "none" => hbb_common::bail!("kiosk not supported on Windows {} {} ({})", c.major, c.edition, c.reason),
            "basic" => {
                let r = basic_lock(app)?;
                INFO.lock().unwrap().0 = None;
                return Ok(r);
            }
            _ => {}
        }
        let own = std::env::current_exe()?.to_string_lossy().to_string();
        let xml = kiosk_xml(app, &own, p["name"].as_str().unwrap_or("Adoluna POS"));
        let x = dl_dir().join("kiosk.xml");
        std::fs::write(&x, xml)?;
        run_ps(&format!(
            "{AA_GET}\r\n$x=[IO.File]::ReadAllText('{}')\r\n$o.Configuration=[System.Net.WebUtility]::HtmlEncode($x)\r\nSet-CimInstance -CimInstance $o",
            x.to_string_lossy().replace('\'', "''")
        ))?;
        if p["reboot"].as_bool().unwrap_or(true) {
            reboot()?;
            return Ok(Some("kiosk configured, rebooting".into()));
        }
        Ok(Some("kiosk configured (takes effect at next sign-in)".into()))
    }

    #[cfg(windows)]
    pub fn win_kiosk_unlock(p: &Value) -> ResultType<Option<String>> {
        basic_unlock().ok();
        INFO.lock().unwrap().0 = None;
        if caps_now().kiosk != "full" {
            if p["reboot"].as_bool().unwrap_or(true) {
                reboot()?;
                return Ok(Some("basic lock removed, rebooting".into()));
            }
            return Ok(Some("basic lock removed (taskbar returns at next sign-in)".into()));
        }
        run_ps(&format!("{AA_GET}\r\n$o.Configuration=$null\r\nSet-CimInstance -CimInstance $o"))?;
        if p["reboot"].as_bool().unwrap_or(true) {
            reboot()?;
            return Ok(Some("kiosk cleared, rebooting".into()));
        }
        Ok(Some("kiosk cleared (takes effect at next sign-in)".into()))
    }

    // ---- b82 Windows kiosk watcher (runs in the SYSTEM service; the limited-user kiosk.ps1 talks to it
    // through files under %ProgramData%\AdolunaSupport). Verifies unlock codes/OTP, owns the pause file. ----
    #[cfg(windows)]
    fn hmac_sha256(key: &[u8], msg: &[u8]) -> [u8; 32] {
        let mut k = [0u8; 64];
        if key.len() > 64 {
            let d = Sha256::digest(key);
            k[..32].copy_from_slice(&d);
        } else {
            k[..key.len()].copy_from_slice(key);
        }
        let mut ipad = [0x36u8; 64];
        let mut opad = [0x5cu8; 64];
        for i in 0..64 { ipad[i] ^= k[i]; opad[i] ^= k[i]; }
        let mut h = Sha256::new();
        h.update(&ipad[..]); h.update(msg);
        let inner = h.finalize();
        let mut h2 = Sha256::new();
        h2.update(&opad[..]); h2.update(&inner);
        let mut out = [0u8; 32];
        out.copy_from_slice(&h2.finalize());
        out
    }

    #[cfg(windows)]
    fn otp_at(hexkey: &str, dev: &str, step: i64) -> Option<String> {
        let key = hex::decode(hexkey).ok()?;
        let d = hmac_sha256(&key, format!("adoluna-otp-v1:{dev}:{step}").as_bytes());
        let v = ((d[0] as u32 & 0x7f) << 24) | ((d[1] as u32) << 16) | ((d[2] as u32) << 8) | d[3] as u32;
        Some(format!("{:08}", v % 100_000_000))
    }

    #[cfg(windows)]
    fn verify_unlock(code: &str) -> &'static str {
        let now = now();
        // 5 wrong tries -> 5 minute lockout
        let locked_until: i64 = Config::get_option("adoluna-uc-lock").parse().unwrap_or(0);
        if locked_until > now { return "locked"; }
        let mut ok = false;
        if code.len() == 6 {
            let h = Config::get_option("adoluna-uc-h");
            let s = Config::get_option("adoluna-uc-s");
            if !h.is_empty() {
                let got = format!("{:x}", Sha256::digest(format!("{s}{code}").as_bytes()));
                ok = got == h.to_lowercase();
            }
        } else if code.len() == 8 {
            let k = Config::get_option("adoluna-otp-k");
            if !k.is_empty() {
                let dev = Config::get_id();
                let used = Config::get_option("adoluna-otp-used");
                if !used.split(',').any(|u| u == code) {
                    let step = now / 600;
                    for st in [step - 1, step, step + 1] {
                        if otp_at(&k, &dev, st).as_deref() == Some(code) { ok = true; break; }
                    }
                    if ok {
                        let mut list: Vec<&str> = used.split(',').filter(|x| !x.is_empty()).collect();
                        list.push(code);
                        if list.len() > 30 { let n = list.len() - 30; list.drain(0..n); }
                        Config::set_option("adoluna-otp-used".into(), list.join(","));
                    }
                }
            }
        }
        if ok {
            Config::set_option("adoluna-uc-fails".into(), "0".into());
            "ok"
        } else {
            let f: i64 = Config::get_option("adoluna-uc-fails").parse().unwrap_or(0);
            let f = f + 1;
            Config::set_option("adoluna-uc-fails".into(), f.to_string());
            if f >= 5 {
                Config::set_option("adoluna-uc-lock".into(), (now + 300).to_string());
                Config::set_option("adoluna-uc-fails".into(), "0".into());
                return "locked";
            }
            "bad"
        }
    }

    #[cfg(windows)]
    fn kiosk_basic_active() -> bool {
        run_ps("if(Get-Command schtasks -EA 0){ schtasks /query /tn AdolunaKiosk 2>$null | Out-Null; if($LASTEXITCODE -eq 0){'1'} }")
            .map(|x| !x.trim().is_empty()).unwrap_or(false)
    }

    #[cfg(windows)]
    pub fn ensure_kiosk_watcher() {
        use std::sync::atomic::{AtomicBool, Ordering};
        static STARTED: AtomicBool = AtomicBool::new(false);
        if STARTED.swap(true, Ordering::SeqCst) { return; }
        std::thread::spawn(|| {
            let dir = kiosk_dir();
            let reqdir = dir.join("kiosk-req");
            // a reboot always relocks: discard any pause left from before this process started
            std::fs::remove_file(dir.join("kiosk-pause")).ok();
            let mut until: i64 = 0;
            let mut app = String::new();
            loop {
                std::thread::sleep(std::time::Duration::from_secs(1));
                if !kiosk_basic_active() { until = 0; continue; }
                // mark remote activity so the loop stops forcing windows to the front
                if !crate::updater::has_no_active_conns() {
                    std::fs::write(dir.join("kiosk-remote"), b"1").ok();
                }
                // a relock request from the float / 立即锁定
                let relock = reqdir.join("relock");
                if relock.exists() {
                    std::fs::remove_file(&relock).ok();
                    until = 0;
                    std::fs::remove_file(dir.join("kiosk-pause")).ok();
                }
                // an unlock request (nonce|code)
                let unlock = reqdir.join("unlock");
                if let Ok(req) = std::fs::read_to_string(&unlock) {
                    std::fs::remove_file(&unlock).ok();
                    if let Some((nonce, code)) = req.trim().split_once('|') {
                        let r = verify_unlock(code.trim());
                        if r == "ok" {
                            until = now() + 30 * 60;
                            std::fs::write(dir.join("kiosk-pause"), format!("{until}|{app}", app = &app)).ok();
                        }
                        std::fs::write(dir.join("kiosk-result"), format!("{nonce}|{r}")).ok();
                    }
                }
                // expiry
                if until > 0 && now() >= until {
                    until = 0;
                    std::fs::remove_file(dir.join("kiosk-pause")).ok();
                }
                let _ = &mut app;
            }
        });
    }
}

/// macOS root LaunchDaemon: pick up an update handed over by the user-level server (which already
/// verified the signed command).  As a second guard, root only accepts a download located on the
/// configured API server and pinned by sha256.
#[cfg(target_os = "macos")]
pub fn mac_root_poll() {
    std::thread::spawn(|| loop {
        std::thread::sleep(std::time::Duration::from_secs(60));
        let f = std::path::Path::new("/Users/Shared/AdolunaSupport/update.json");
        let Ok(s) = std::fs::read_to_string(f) else { continue };
        std::fs::remove_file(f).ok();
        let Ok(p) = serde_json::from_str::<Value>(&s) else { continue };
        if update_decision(&p, BUILD) != Ok(true) {
            log::info!("adoluna mac update: not newer than b{BUILD}, ignored");
            continue;
        }
        let api = crate::ui_interface::get_api_server();
        let url = p["url"].as_str().unwrap_or_default();
        if api.is_empty() || !url.starts_with(&format!("{api}/")) {
            log::warn!("adoluna mac update: url not on API server, ignored");
            continue;
        }
        match desktop::download(&p) {
            Ok(path) => {
                let r = mac_install_dmg(&path.to_string_lossy());
                log::info!("adoluna mac update: {r:?}");
            }
            Err(e) => log::warn!("adoluna mac update download: {e}"),
        }
    });
}

#[cfg(target_os = "macos")]
fn mac_install_dmg(dmg: &str) -> ResultType<()> {
    let app = format!("{}.app", crate::get_app_name());
    let script = format!(
        r#"set -e
M=$(/usr/bin/mktemp -d /tmp/.adl-mnt-XXXXXX)
/usr/bin/hdiutil attach -nobrowse -readonly -mountpoint "$M" "{dmg}"
/bin/rm -rf "/Applications/{app}.new"
/usr/bin/ditto "$M/{app}" "/Applications/{app}.new"
/usr/bin/hdiutil detach "$M" -force || true
/bin/rm -rf "/Applications/{app}.old"
/bin/mv "/Applications/{app}" "/Applications/{app}.old" || true
/bin/mv "/Applications/{app}.new" "/Applications/{app}"
/bin/rm -rf "/Applications/{app}.old" "{dmg}"
for p in /Library/LaunchDaemons/com.carriez.*.plist /Library/LaunchDaemons/com.adoluna*.plist; do [ -f "$p" ] && /bin/launchctl kickstart -k system/$(/usr/bin/basename "$p" .plist) || true; done
"#
    );
    std::process::Command::new("/bin/sh").args(["-c", &script]).spawn()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    #[cfg(not(target_os = "android"))]
    #[test]
    fn staff_pin_payload_checks() {
        let ok = super::staff_pin_payload(&serde_json::from_str::<Value>(&format!(r#"{{"s":"00112233445566778899","h":"{}","i":20000,"v":3}}"#, "ab".repeat(32))).unwrap()).unwrap();
        assert!(ok.contains("\"i\":20000"));
        assert_eq!(super::staff_pin_payload(&json!({"h":""})).unwrap(), "");
        assert!(super::staff_pin_payload(&json!({"s":"zz","h":"00","i":5})).is_err());
        assert!(super::staff_pin_payload(&json!("nope")).is_err());
    }

    #[test]
    fn update_compares_build_numbers_only() {
        use super::update_decision as d;
        assert_eq!(d(&json!({"build": 81, "ver": "1.5.0"}), 80), Ok(true));
        assert_eq!(d(&json!({"build": 80, "ver": "1.5.1"}), 80), Ok(false));
        assert_eq!(d(&json!({"build": 79}), 80), Ok(false));
        assert!(d(&json!({"ver": "1.5.0-b81"}), 80).is_err());
        assert!(d(&json!({"build": "81"}), 80).is_err());
        assert!(d(&json!({"build": 0}), 80).is_err());
        assert_eq!(super::BUILD, 81);
        assert_eq!(super::version_label(), format!("{} (b81)", crate::VERSION));
    }

    #[test]
    fn win_caps_matrix() {
        use super::desktop::win_caps;
        let c = |e, b| { let w = win_caps(e, b); (w.major, w.edition, w.install, w.kiosk, w.reason) };
        assert_eq!(c("Professional", 22631), ("11", "Pro", true, "full", ""));
        assert_eq!(c("Enterprise", 26100), ("11", "Enterprise", true, "full", ""));
        assert_eq!(c("Education", 22000), ("11", "Education", true, "full", ""));
        assert_eq!(c("Core", 22631), ("11", "Home", true, "none", "needs_pro"));
        assert_eq!(c("Professional", 19045), ("10", "Pro", true, "basic", ""));
        assert_eq!(c("CoreSingleLanguage", 19045), ("10", "Home", true, "none", "needs_pro"));
        assert_eq!(c("Professional", 9600), ("8.1", "Pro", false, "none", "os_unsupported"));
        assert_eq!(c("Ultimate", 7601), ("7", "Other", false, "none", "os_unsupported"));
    }
    use super::*;
    use hbb_common::base64::{engine::general_purpose::STANDARD, Engine};
    use hbb_common::sodiumoxide::crypto::sign;

    fn mk(sk: &sign::SecretKey, id: &str, n: i64, k: &str, p: &str, e: i64) -> Value {
        let sig = STANDARD.encode(sign::sign_detached(cmd_msg(id, n, k, p, e).as_bytes(), sk).to_bytes());
        json!({"n": n, "k": k, "p": p, "e": e, "sig": sig})
    }

    #[test]
    fn cmd_verify() {
        let (pk, sk) = sign::gen_keypair();
        let c = mk(&sk, "123", 5, "reboot", "{}", 2000);
        assert_eq!(verify_cmd_with(&pk, &c, "123", 0, 1000).unwrap().unwrap().k, "reboot");
        assert!(verify_cmd_with(&pk, &c, "123", 5, 1000).unwrap().is_none()); // replay
        assert!(verify_cmd_with(&pk, &c, "999", 0, 1000).is_err()); // other device
        assert!(verify_cmd_with(&pk, &c, "123", 0, 3000).is_err()); // expired
        let mut t = c.clone();
        t["k"] = json!("install");
        assert!(verify_cmd_with(&pk, &t, "123", 0, 1000).is_err()); // tampered
        let (_, sk2) = sign::gen_keypair();
        assert!(verify_cmd_with(&pk, &mk(&sk2, "123", 6, "reboot", "{}", 2000), "123", 0, 1000).is_err());
        let p = r#"{"pkg":"com.pos"}"#;
        let c = verify_cmd_with(&pk, &mk(&sk, "123", 7, "kiosk_lock", p, 2000), "123", 6, 1000).unwrap().unwrap();
        assert_eq!(c.p["pkg"], "com.pos");
    }

    #[cfg(not(target_os = "android"))]
    #[test]
    fn kiosk_xml_escapes() {
        let x = desktop::kiosk_xml("C:\\POS\\a&b.exe", "C:\\Program Files\\Adoluna Support\\adoluna.exe", "P<OS>");
        assert!(x.contains("a&amp;b.exe") && x.contains("P&lt;OS&gt;") && x.contains("AutoLaunch=\"true\""));
    }
}
