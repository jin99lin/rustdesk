use std::{
    collections::HashMap,
    iter::FromIterator,
    sync::{Arc, Mutex},
};

use sciter::Value;

use hbb_common::{
    allow_err,
    config::{LocalConfig, PeerConfig},
    log,
};

#[cfg(not(feature = "flutter"))]
use crate::ui_session_interface::Session;
use crate::{common::get_app_name, ipc, ui_interface::*};

mod cm;
#[cfg(feature = "inline")]
pub mod inline;
pub mod remote;

#[allow(dead_code)]
type Status = (i32, bool, i64, String);

lazy_static::lazy_static! {
    // stupid workaround for https://sciter.com/forums/topic/crash-on-latest-tis-mac-sdk-sometimes/
    static ref STUPID_VALUES: Mutex<Vec<Arc<Vec<Value>>>> = Default::default();
}

#[cfg(not(feature = "flutter"))]
lazy_static::lazy_static! {
    pub static ref CUR_SESSION: Arc<Mutex<Option<Session<remote::SciterHandler>>>> = Default::default();
}

struct UIHostHandler;

pub fn start(args: &mut [String]) {
    #[cfg(target_os = "macos")]
    crate::platform::delegate::show_dock();
    #[cfg(all(target_os = "linux", feature = "inline"))]
    {
        let app_dir = std::env::var("APPDIR").unwrap_or("".to_string());
        let mut so_path = "/usr/share/rustdesk/libsciter-gtk.so".to_owned();
        for (prefix, dir) in [
            ("", "/usr"),
            ("", "/app"),
            (&app_dir, "/usr"),
            (&app_dir, "/app"),
        ]
        .iter()
        {
            let path = format!("{prefix}{dir}/share/rustdesk/libsciter-gtk.so");
            if std::path::Path::new(&path).exists() {
                so_path = path;
                break;
            }
        }
        sciter::set_library(&so_path).ok();
    }
    #[cfg(windows)]
    // Check if there is a sciter.dll nearby.
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            let sciter_dll_path = parent.join("sciter.dll");
            if sciter_dll_path.exists() {
                // Try to set the sciter dll.
                let p = sciter_dll_path.to_string_lossy().to_string();
                log::debug!("Found dll:{}, \n {:?}", p, sciter::set_library(&p));
            }
        }
    }
    // https://github.com/c-smile/sciter-sdk/blob/master/include/sciter-x-types.h
    // https://github.com/rustdesk/rustdesk/issues/132#issuecomment-886069737
    #[cfg(windows)]
    allow_err!(sciter::set_options(sciter::RuntimeOptions::GfxLayer(
        sciter::GFX_LAYER::WARP
    )));
    use sciter::SCRIPT_RUNTIME_FEATURES::*;
    allow_err!(sciter::set_options(sciter::RuntimeOptions::ScriptFeatures(
        ALLOW_FILE_IO as u8 | ALLOW_SOCKET_IO as u8 | ALLOW_EVAL as u8 | ALLOW_SYSINFO as u8
    )));
    let mut frame = sciter::WindowBuilder::main_window().create();
    #[cfg(windows)]
    allow_err!(sciter::set_options(sciter::RuntimeOptions::UxTheming(true)));
    frame.set_title(&crate::get_app_display_name());
    #[cfg(target_os = "macos")]
    crate::platform::delegate::make_menubar(frame.get_host(), args.is_empty());
    #[cfg(windows)]
    crate::platform::try_set_window_foreground(frame.get_hwnd() as _);
    let page;
    if args.len() > 1 && args[0] == "--play" {
        args[0] = "--connect".to_owned();
        let path: std::path::PathBuf = (&args[1]).into();
        let id = path
            .file_stem()
            .map(|p| p.to_str().unwrap_or(""))
            .unwrap_or("")
            .to_owned();
        args[1] = id;
    }
    if args.is_empty() {
        std::thread::spawn(move || check_zombie());
        crate::common::check_software_update();
        frame.event_handler(UI {});
        frame.sciter_handler(UIHostHandler {});
        page = "index.html";
        // Start pulse audio local server.
        #[cfg(target_os = "linux")]
        std::thread::spawn(crate::ipc::start_pa);
    } else if args[0] == "--install" {
        frame.event_handler(UI {});
        frame.sciter_handler(UIHostHandler {});
        page = "install.html";
    } else if args[0] == "--cm" {
        frame.register_behavior("connection-manager", move || {
            Box::new(cm::SciterConnectionManager::new())
        });
        page = "cm.html";
        *cm::HIDE_CM.lock().unwrap() = crate::ipc::get_config("hide_cm")
            .ok()
            .flatten()
            .unwrap_or_default()
            == "true";
    } else if (args[0] == "--connect"
        || args[0] == "--file-transfer"
        || args[0] == "--port-forward"
        || args[0] == "--rdp")
        && args.len() > 1
    {
        #[cfg(windows)]
        {
            let hw = frame.get_host().get_hwnd();
            crate::platform::windows::enable_lowlevel_keyboard(hw as _);
        }
        let mut iter = args.iter();
        let Some(cmd) = iter.next() else {
            log::error!("Failed to get cmd arg");
            return;
        };
        let cmd = cmd.to_owned();
        let Some(id) = iter.next() else {
            log::error!("Failed to get id arg");
            return;
        };
        let id = id.to_owned();
        let pass = iter.next().unwrap_or(&"".to_owned()).clone();
        let args: Vec<String> = iter.map(|x| x.clone()).collect();
        frame.set_title(&id);
        frame.register_behavior("native-remote", move || {
            let handler =
                remote::SciterSession::new(cmd.clone(), id.clone(), pass.clone(), args.clone());
            #[cfg(not(feature = "flutter"))]
            {
                *CUR_SESSION.lock().unwrap() = Some(handler.inner());
            }
            Box::new(handler)
        });
        page = "remote.html";
    } else {
        log::error!("Wrong command: {:?}", args);
        return;
    }
    #[cfg(feature = "inline")]
    {
        let html = if page == "index.html" {
            inline::get_index()
        } else if page == "cm.html" {
            inline::get_cm()
        } else if page == "install.html" {
            inline::get_install()
        } else {
            inline::get_remote()
        };
        frame.load_html(html.as_bytes(), Some(page));
    }
    #[cfg(not(feature = "inline"))]
    frame.load_file(&format!(
        "file://{}/src/ui/{}",
        std::env::current_dir()
            .map(|c| c.display().to_string())
            .unwrap_or("".to_owned()),
        page
    ));
    let hide_cm = *cm::HIDE_CM.lock().unwrap();
    if !args.is_empty() && args[0] == "--cm" && hide_cm {
        // run_app calls expand(show) + run_loop, we use collapse(hide) + run_loop instead to create a hidden window
        frame.collapse(true);
        frame.run_loop();
        return;
    }
    frame.run_app();
}

struct UI {}

impl UI {
    fn recent_sessions_updated(&self) -> bool {
        recent_sessions_updated()
    }

    fn get_id(&self) -> String {
        ipc::get_id()
    }

    fn temporary_password(&mut self) -> String {
        temporary_password()
    }

    fn update_temporary_password(&self) {
        update_temporary_password()
    }

    fn set_permanent_password(&self, password: String) {
        let _ = set_permanent_password_with_result(password);
    }

    fn is_local_permanent_password_set(&self) -> bool {
        is_local_permanent_password_set()
    }

    fn is_permanent_password_set(&self) -> bool {
        is_permanent_password_set()
    }

    fn get_remote_id(&mut self) -> String {
        LocalConfig::get_remote_id()
    }

    fn set_remote_id(&mut self, id: String) {
        LocalConfig::set_remote_id(&id);
    }

    fn goto_install(&mut self) {
        goto_install();
    }

    fn install_me(&mut self, _options: String, _path: String) {
        install_me(_options, _path, false, false);
    }

    fn update_me(&self, _path: String) {
        update_me(_path);
    }

    fn run_without_install(&self) {
        run_without_install();
    }

    fn show_run_without_install(&self) -> bool {
        show_run_without_install()
    }

    fn get_license(&self) -> String {
        get_license()
    }

    fn get_option(&self, key: String) -> String {
        get_option(key)
    }

    fn get_local_option(&self, key: String) -> String {
        get_local_option(key)
    }

    fn set_local_option(&self, key: String, value: String) {
        set_local_option(key, value);
    }

    fn peer_has_password(&self, id: String) -> bool {
        peer_has_password(id)
    }

    fn forget_password(&self, id: String) {
        forget_password(id)
    }

    fn get_peer_option(&self, id: String, name: String) -> String {
        get_peer_option(id, name)
    }

    fn set_peer_option(&self, id: String, name: String, value: String) {
        set_peer_option(id, name, value)
    }

    fn using_public_server(&self) -> bool {
        crate::using_public_server()
    }

    fn is_incoming_only(&self) -> bool {
        hbb_common::config::is_incoming_only()
    }

    pub fn is_outgoing_only(&self) -> bool {
        hbb_common::config::is_outgoing_only()
    }

    pub fn is_custom_client(&self) -> bool {
        crate::common::is_custom_client()
    }

    pub fn is_disable_settings(&self) -> bool {
        hbb_common::config::is_disable_settings()
    }

    pub fn is_disable_account(&self) -> bool {
        hbb_common::config::is_disable_account()
    }

    pub fn is_disable_installation(&self) -> bool {
        hbb_common::config::is_disable_installation()
    }

    pub fn is_disable_ab(&self) -> bool {
        hbb_common::config::is_disable_ab()
    }

    fn get_options(&self) -> Value {
        let hashmap: HashMap<String, String> =
            serde_json::from_str(&get_options()).unwrap_or_default();
        let mut m = Value::map();
        for (k, v) in hashmap {
            m.set_item(k, v);
        }
        m
    }

    fn test_if_valid_server(&self, host: String, test_with_proxy: bool) -> String {
        test_if_valid_server(host, test_with_proxy)
    }

    fn get_sound_inputs(&self) -> Value {
        Value::from_iter(get_sound_inputs())
    }

    fn set_options(&self, v: Value) {
        let mut m = HashMap::new();
        for (k, v) in v.items() {
            if let Some(k) = k.as_string() {
                if let Some(v) = v.as_string() {
                    if !v.is_empty() {
                        m.insert(k, v);
                    }
                }
            }
        }
        set_options(m);
    }

    fn set_option(&self, key: String, value: String) {
        set_option(key, value);
    }

    fn install_path(&mut self) -> String {
        install_path()
    }

    fn install_options(&self) -> String {
        install_options()
    }

    fn get_socks(&self) -> Value {
        Value::from_iter(get_socks())
    }

    fn set_socks(&self, proxy: String, username: String, password: String) {
        set_socks(proxy, username, password)
    }

    fn is_installed(&self) -> bool {
        is_installed()
    }

    fn get_supported_privacy_mode_impls(&self) -> String {
        serde_json::to_string(&crate::privacy_mode::get_supported_privacy_mode_impl())
            .unwrap_or_default()
    }

    fn is_root(&self) -> bool {
        is_root()
    }

    fn is_release(&self) -> bool {
        #[cfg(not(debug_assertions))]
        return true;
        #[cfg(debug_assertions)]
        return false;
    }

    fn is_share_rdp(&self) -> bool {
        is_share_rdp()
    }

    fn set_share_rdp(&self, _enable: bool) {
        set_share_rdp(_enable);
    }

    fn is_installed_lower_version(&self) -> bool {
        is_installed_lower_version()
    }

    fn closing(&mut self, x: i32, y: i32, w: i32, h: i32) {
        crate::server::input_service::fix_key_down_timeout_at_exit();
        LocalConfig::set_size(x, y, w, h);
    }

    fn get_size(&mut self) -> Value {
        let s = LocalConfig::get_size();
        let mut v = Vec::new();
        v.push(s.0);
        v.push(s.1);
        v.push(s.2);
        v.push(s.3);
        Value::from_iter(v)
    }

    fn get_mouse_time(&self) -> f64 {
        get_mouse_time()
    }

    fn check_mouse_time(&self) {
        check_mouse_time()
    }

    fn get_connect_status(&mut self) -> Value {
        let mut v = Value::array(0);
        let x = get_connect_status();
        v.push(x.status_num);
        v.push(x.key_confirmed);
        v.push(x.id);
        v
    }

    #[inline]
    fn get_peer_value(id: String, p: PeerConfig) -> Value {
        let values = vec![
            id,
            p.info.username.clone(),
            p.info.hostname.clone(),
            p.info.platform.clone(),
            p.options.get("alias").unwrap_or(&"".to_owned()).to_owned(),
        ];
        Value::from_iter(values)
    }

    fn get_peer(&self, id: String) -> Value {
        let c = get_peer(id.clone());
        Self::get_peer_value(id, c)
    }

    fn get_fav(&self) -> Value {
        Value::from_iter(get_fav())
    }

    fn store_fav(&self, fav: Value) {
        let mut tmp = vec![];
        fav.values().for_each(|v| {
            if let Some(v) = v.as_string() {
                if !v.is_empty() {
                    tmp.push(v);
                }
            }
        });
        store_fav(tmp);
    }

    fn get_recent_sessions(&mut self) -> Value {
        // to-do: limit number of recent sessions, and remove old peer file
        let peers: Vec<Value> = PeerConfig::peers(None)
            .drain(..)
            .map(|p| Self::get_peer_value(p.0, p.2))
            .collect();
        Value::from_iter(peers)
    }

    fn get_icon(&mut self) -> String {
        get_icon()
    }

    fn remove_peer(&mut self, id: String) {
        PeerConfig::remove(&id);
    }

    fn remove_discovered(&mut self, id: String) {
        remove_discovered(id);
    }

    fn send_wol(&mut self, id: String) {
        crate::lan::send_wol(id)
    }

    fn new_remote(&mut self, id: String, remote_type: String, force_relay: bool) {
        new_remote(id, remote_type, force_relay)
    }

    fn is_process_trusted(&mut self, _prompt: bool) -> bool {
        is_process_trusted(_prompt)
    }

    fn is_can_screen_recording(&mut self, _prompt: bool) -> bool {
        is_can_screen_recording(_prompt)
    }

    fn is_installed_daemon(&mut self, _prompt: bool) -> bool {
        is_installed_daemon(_prompt)
    }

    fn get_error(&mut self) -> String {
        get_error()
    }

    fn is_login_wayland(&mut self) -> bool {
        is_login_wayland()
    }

    fn current_is_wayland(&mut self) -> bool {
        current_is_wayland()
    }

    fn get_software_update_url(&self) -> String {
        crate::SOFTWARE_UPDATE_URL.lock().unwrap().clone()
    }

    fn get_new_version(&self) -> String {
        get_new_version()
    }

    fn get_version(&self) -> String {
        get_version()
    }

    fn get_fingerprint(&self) -> String {
        get_fingerprint()
    }

    fn get_app_name(&self) -> String {
        get_app_name()
    }

    fn get_software_ext(&self) -> String {
        #[cfg(windows)]
        let p = "exe";
        #[cfg(target_os = "macos")]
        let p = "dmg";
        #[cfg(target_os = "linux")]
        let p = "deb";
        p.to_owned()
    }

    fn get_software_store_path(&self) -> String {
        let mut p = std::env::temp_dir();
        let name = crate::SOFTWARE_UPDATE_URL
            .lock()
            .unwrap()
            .split("/")
            .last()
            .map(|x| x.to_owned())
            .unwrap_or(crate::get_app_name());
        p.push(name);
        format!("{}.{}", p.to_string_lossy(), self.get_software_ext())
    }

    fn create_shortcut(&self, _id: String) {
        #[cfg(windows)]
        create_shortcut(_id)
    }

    fn discover(&self) {
        std::thread::spawn(move || {
            allow_err!(crate::lan::discover());
        });
    }

    fn get_lan_peers(&self) -> String {
        // let peers = get_lan_peers()
        //     .into_iter()
        //     .map(|mut peer| {
        //         (
        //             peer.remove("id").unwrap_or_default(),
        //             peer.remove("username").unwrap_or_default(),
        //             peer.remove("hostname").unwrap_or_default(),
        //             peer.remove("platform").unwrap_or_default(),
        //         )
        //     })
        //     .collect::<Vec<(String, String, String, String)>>();
        serde_json::to_string(&get_lan_peers()).unwrap_or_default()
    }

    fn get_uuid(&self) -> String {
        get_uuid()
    }

    fn open_url(&self, url: String) {
        #[cfg(windows)]
        let p = "explorer";
        #[cfg(target_os = "macos")]
        let p = "open";
        #[cfg(target_os = "linux")]
        let p = if std::path::Path::new("/usr/bin/firefox").exists() {
            "firefox"
        } else {
            "xdg-open"
        };
        allow_err!(std::process::Command::new(p).arg(url).spawn());
    }

    fn change_id(&self, id: String) {
        reset_async_job_status();
        let old_id = self.get_id();
        change_id_shared(id, old_id);
    }

    fn http_request(&self, url: String, method: String, body: Option<String>, header: String) {
        http_request(url, method, body, header)
    }

    fn post_request(&self, url: String, body: String, header: String) {
        post_request(url, body, header)
    }

    fn is_ok_change_id(&self) -> bool {
        hbb_common::machine_uid::get().is_ok()
    }

    fn get_async_job_status(&self) -> String {
        get_async_job_status()
    }

    fn get_http_status(&self, url: String) -> Option<String> {
        get_async_http_status(url)
    }

    fn t(&self, name: String) -> String {
        crate::client::translate(name)
    }

    fn is_xfce(&self) -> bool {
        crate::platform::is_xfce()
    }

    fn get_api_server(&self) -> String {
        get_api_server()
    }

    fn has_hwcodec(&self) -> bool {
        has_hwcodec()
    }

    fn has_vram(&self) -> bool {
        has_vram()
    }

    fn get_langs(&self) -> String {
        get_langs()
    }

    fn video_save_directory(&self, root: bool) -> String {
        video_save_directory(root)
    }

    fn handle_relay_id(&self, id: String) -> String {
        handle_relay_id(&id).to_owned()
    }

    fn get_login_device_info(&self) -> String {
        get_login_device_info_json()
    }

    fn support_remove_wallpaper(&self) -> bool {
        support_remove_wallpaper()
    }

    fn has_valid_2fa(&self) -> bool {
        has_valid_2fa()
    }

    fn generate2fa(&self) -> String {
        generate2fa()
    }

    pub fn verify2fa(&self, code: String) -> bool {
        verify2fa(code)
    }

    fn verify_login(&self, raw: String, id: String) -> bool {
        crate::verify_login(&raw, &id)
    }

    fn generate_2fa_img_src(&self, data: String) -> String {
        let v = qrcode_generator::to_png_to_vec(data, qrcode_generator::QrCodeEcc::Low, 128)
            .unwrap_or_default();
        let s = hbb_common::sodiumoxide::base64::encode(
            v,
            hbb_common::sodiumoxide::base64::Variant::Original,
        );
        format!("data:image/png;base64,{s}")
    }

    pub fn check_hwcodec(&self) {
        check_hwcodec()
    }

    fn is_option_fixed(&self, key: String) -> bool {
        crate::ui_interface::is_option_fixed(&key)
    }

    fn get_builtin_option(&self, key: String) -> String {
        crate::ui_interface::get_builtin_option(&key)
    }

    fn is_remote_modify_enabled_by_control_permissions(&self) -> String {
        match crate::ui_interface::is_remote_modify_enabled_by_control_permissions() {
            Some(true) => "true",
            Some(false) => "false",
            None => "",
        }
        .to_string()
    }
}

impl sciter::EventHandler for UI {
    sciter::dispatch_script_call! {
        fn t(String);
        fn get_api_server();
        fn is_xfce();
        fn using_public_server();
        fn is_custom_client();
        fn is_outgoing_only();
        fn is_incoming_only();
        fn is_disable_settings();
        fn is_disable_account();
        fn is_disable_installation();
        fn is_disable_ab();
        fn get_id();
        fn temporary_password();
        fn update_temporary_password();
        fn set_permanent_password(String);
        fn is_local_permanent_password_set();
        fn is_permanent_password_set();
        fn get_remote_id();
        fn set_remote_id(String);
        fn closing(i32, i32, i32, i32);
        fn get_size();
        fn new_remote(String, String, bool);
        fn send_wol(String);
        fn remove_peer(String);
        fn remove_discovered(String);
        fn get_connect_status();
        fn get_mouse_time();
        fn check_mouse_time();
        fn get_recent_sessions();
        fn get_peer(String);
        fn get_fav();
        fn store_fav(Value);
        fn recent_sessions_updated();
        fn get_icon();
        fn install_me(String, String);
        fn is_installed();
        fn get_supported_privacy_mode_impls();
        fn is_root();
        fn is_release();
        fn set_socks(String, String, String);
        fn get_socks();
        fn is_share_rdp();
        fn set_share_rdp(bool);
        fn is_installed_lower_version();
        fn install_path();
        fn install_options();
        fn goto_install();
        fn is_process_trusted(bool);
        fn is_can_screen_recording(bool);
        fn is_installed_daemon(bool);
        fn get_error();
        fn is_login_wayland();
        fn current_is_wayland();
        fn get_options();
        fn get_option(String);
        fn get_local_option(String);
        fn set_local_option(String, String);
        fn get_peer_option(String, String);
        fn peer_has_password(String);
        fn forget_password(String);
        fn set_peer_option(String, String, String);
        fn get_license();
        fn test_if_valid_server(String, bool);
        fn get_sound_inputs();
        fn set_options(Value);
        fn set_option(String, String);
        fn get_software_update_url();
        fn get_new_version();
        fn get_version();
        fn get_fingerprint();
        fn update_me(String);
        fn show_run_without_install();
        fn run_without_install();
        fn get_app_name();
        fn get_software_store_path();
        fn get_software_ext();
        fn open_url(String);
        fn change_id(String);
        fn get_async_job_status();
        fn post_request(String, String, String);
        fn is_ok_change_id();
        fn create_shortcut(String);
        fn discover();
        fn get_lan_peers();
        fn get_uuid();
        fn has_hwcodec();
        fn has_vram();
        fn get_langs();
        fn video_save_directory(bool);
        fn handle_relay_id(String);
        fn get_login_device_info();
        fn support_remove_wallpaper();
        fn has_valid_2fa();
        fn generate2fa();
        fn generate_2fa_img_src(String);
        fn verify2fa(String);
        fn check_hwcodec();
        fn verify_login(String, String);
        fn is_option_fixed(String);
        fn get_builtin_option(String);
        fn is_remote_modify_enabled_by_control_permissions();
    }
}

impl sciter::host::HostHandler for UIHostHandler {
    fn on_graphics_critical_failure(&mut self) {
        log::error!("Critical rendering error: e.g. DirectX gfx driver error. Most probably bad gfx drivers.");
    }
}

#[cfg(not(target_os = "linux"))]
fn get_sound_inputs() -> Vec<String> {
    let mut out = Vec::new();
    use cpal::traits::{DeviceTrait, HostTrait};
    let host = cpal::default_host();
    if let Ok(devices) = host.devices() {
        for device in devices {
            if device.default_input_config().is_err() {
                continue;
            }
            if let Ok(name) = device.name() {
                out.push(name);
            }
        }
    }
    out
}

#[cfg(target_os = "linux")]
fn get_sound_inputs() -> Vec<String> {
    crate::platform::linux::get_pa_sources()
        .drain(..)
        .map(|x| x.1)
        .collect()
}

// sacrifice some memory
pub fn value_crash_workaround(values: &[Value]) -> Arc<Vec<Value>> {
    let persist = Arc::new(values.to_vec());
    STUPID_VALUES.lock().unwrap().push(persist.clone());
    persist
}

pub fn get_icon() -> String {
    // 128x128
    #[cfg(target_os = "macos")]
    // 128x128 on 160x160 canvas, then shrink to 128, mac looks better with padding
    {
        "data:image/png;base64,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".into()
    }
    #[cfg(not(target_os = "macos"))] // 128x128 no padding
    {
        "data:image/png;base64,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".into()
    }
}
