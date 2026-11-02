fn main() {
    #[cfg(windows)]
    {
        use std::io::Write;
        let mut res = winres::WindowsResource::new();
        res.set_icon("../../res/icon.ico")
            .set_language(winapi::um::winnt::MAKELANGID(
                winapi::um::winnt::LANG_ENGLISH,
                winapi::um::winnt::SUBLANG_ENGLISH_US,
            ))
            .set_manifest_file("../../res/manifest.xml");
        // b84: stamp the Adoluna build number into the installer exe (FileVersion 1.5.0.<build>,
        // ProductVersion 1.5.0-b<build>). Source: ADOLUNA_BUILD env or the +N in flutter/pubspec.yaml.
        println!("cargo:rerun-if-env-changed=ADOLUNA_BUILD");
        println!("cargo:rerun-if-changed=../../flutter/pubspec.yaml");
        let build: u64 = std::env::var("ADOLUNA_BUILD").ok().filter(|s| !s.trim().is_empty())
            .or_else(|| std::fs::read_to_string("../../flutter/pubspec.yaml").ok().and_then(|s| s.lines()
                .find(|l| l.starts_with("version:")).and_then(|l| l.rsplit('+').next().map(|x| x.trim().to_owned()))))
            .and_then(|s| s.trim().parse().ok()).unwrap_or(0);
        let ver = env!("CARGO_PKG_VERSION");
        let p: Vec<u64> = ver.split('.').map(|x| x.parse().unwrap_or(0)).collect();
        let packed = (p.get(0).copied().unwrap_or(0) << 48) | (p.get(1).copied().unwrap_or(0) << 32)
            | (p.get(2).copied().unwrap_or(0) << 16) | build;
        res.set_version_info(winres::VersionInfo::FILEVERSION, packed)
            .set_version_info(winres::VersionInfo::PRODUCTVERSION, packed)
            .set("FileVersion", &format!("{ver}.{build}"))
            .set("ProductVersion", &format!("{ver}-b{build}"));
        match res.compile() {
            Err(e) => {
                write!(std::io::stderr(), "{}", e).unwrap();
                std::process::exit(1);
            }
            Ok(_) => {}
        }
    }
}
