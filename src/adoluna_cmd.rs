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
        "reboot" => desktop::reboot(),
        "install" => desktop::install(&c.p),
        "update" => desktop::update(&c.p),
        #[cfg(windows)]
        "kiosk_lock" => desktop::win_kiosk_lock(&c.p),
        #[cfg(windows)]
        "kiosk_unlock" => desktop::win_kiosk_unlock(&c.p),
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

#[cfg(not(target_os = "android"))]
mod desktop {
    use super::*;
    use sha2::{Digest, Sha256};
    use std::path::PathBuf;

    lazy_static::lazy_static! {
        static ref INFO: Mutex<(Option<std::time::Instant>, Value)> = Mutex::new((None, Value::Null));
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
        }
        #[cfg(target_os = "macos")]
        {
            let sh = |s: &str| std::process::Command::new("/bin/sh").args(["-c", s]).output().ok()
                .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_owned()).unwrap_or_default();
            v["serial"] = json!(sh("ioreg -c IOPlatformExpertDevice -d 2 | awk -F\\\" '/IOPlatformSerialNumber/{print $4}'"));
            v["model"] = json!(sh("sysctl -n hw.model"));
            v["disk_free_gb"] = json!(sh("df -g / | awk 'NR==2{print $4}'"));
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
        let ver = p["ver"].as_str().unwrap_or_default().to_owned();
        if !ver.is_empty() && ver == crate::VERSION && p["build"].as_i64().is_none() {
            return Ok(Some("already up to date".into()));
        }
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
    const BASIC_POL: &[(&str, &str)] = &[
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "DisableTaskMgr"),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "DisableLockWorkstation"),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "DisableChangePassword"),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "HideFastUserSwitching"),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer", "NoWinKeys"),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer", "NoLogoff"),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer", "NoTrayContextMenu"),
        // Ctrl+Alt+Del screen mitigation: Task Manager / Lock / Switch user / Change password / Sign out already off above.
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer", "NoRun"),
        ("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer", "NoControlPanel"),
        ("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\System", "DisableCMD"),
    ];

    #[cfg(windows)]
    fn basic_lock(app: &str) -> ResultType<Option<String>> {
        let dir = dl_dir().parent().map(|d| d.to_path_buf()).unwrap_or(dl_dir());
        let ps1 = dir.join("kiosk.ps1");
        let name = std::path::Path::new(app).file_stem().map(|x| x.to_string_lossy().to_string()).unwrap_or_default();
        std::fs::write(&ps1, format!(r#"Add-Type -Name W -Namespace A -MemberDefinition '[DllImport("user32.dll")] public static extern int FindWindow(string c,string t); [DllImport("user32.dll")] public static extern bool ShowWindow(int h,int c);'
while ($true) {{
  foreach ($c in 'Shell_TrayWnd','Shell_SecondaryTrayWnd','Button') {{ $h=[A.W]::FindWindow($c,$null); if ($h) {{ [A.W]::ShowWindow($h,0) | Out-Null }} }}
  if (-not (Get-Process -Name '{n}' -ErrorAction SilentlyContinue)) {{ Start-Process -FilePath '{a}' }}
  Start-Sleep -Seconds 3
}}
"#, n = name.replace('\'', "''"), a = app.replace('\'', "''")))?;
        let mut sc = String::new();
        for (k, n) in BASIC_POL {
            sc += &format!("if(-not (Test-Path '{k}')){{New-Item -Path '{k}' -Force|Out-Null}}; Set-ItemProperty -Path '{k}' -Name {n} -Value 1 -Type DWord\r\n");
        }
        sc += &format!("$ErrorActionPreference='Continue'\r\nschtasks /delete /tn AdolunaKiosk /f 2>$null | Out-Null\r\nschtasks /create /tn AdolunaKiosk /sc onlogon /ru BUILTIN\\Users /rl LIMITED /f /tr 'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File {}' | Out-Null\r\nif($LASTEXITCODE -ne 0){{throw 'schtasks create failed'}}\r\nschtasks /run /tn AdolunaKiosk 2>$null | Out-Null", ps1.to_string_lossy());
        run_ps(&sc)?;
        Ok(Some("basic lock on (relaunch + hidden taskbar + hotkeys blocked); full effect after next sign-in".into()))
    }

    #[cfg(windows)]
    fn basic_unlock() -> ResultType<()> {
        let mut sc = String::from("$ErrorActionPreference='Continue'\r\nschtasks /end /tn AdolunaKiosk 2>$null | Out-Null\r\nschtasks /delete /tn AdolunaKiosk /f 2>$null | Out-Null\r\n");
        for (k, n) in BASIC_POL {
            sc += &format!("Remove-ItemProperty -Path '{k}' -Name {n} -ErrorAction SilentlyContinue\r\n");
        }
        run_ps(&sc).map(|_| ())
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
