# Adoluna touch diagnosis (b88). Run in the merchant's signed-in session (not as SYSTEM).
# Logs once per second: remote-connection state, monitors/resolution, touch->monitor mapping,
# and counts of mouse/touch events seen by a low-level mouse hook (physical / injected / touch-signature)
# and by Raw Input per device. Writes C:\ProgramData\AdolunaDiag\touchdiag-<time>.log, then zips logs.
param([int]$Seconds = 180)
$ErrorActionPreference = 'Continue'
$out = 'C:\ProgramData\AdolunaDiag'; New-Item -ItemType Directory -Force $out | Out-Null
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'; $log = Join-Path $out "touchdiag-$ts.log"
Add-Type -ReferencedAssemblies System.Windows.Forms -TypeDefinition @'
using System; using System.Runtime.InteropServices; using System.Windows.Forms; using System.Collections.Generic;
public static class TD {
  public static int Phys, Inj, TouchSig, Down, Up; public static Dictionary<string,int> Raw = new Dictionary<string,int>();
  delegate IntPtr LLP(int c, IntPtr w, IntPtr l);
  [StructLayout(LayoutKind.Sequential)] struct MS { public int x, y; public uint data, flags, time; public UIntPtr extra; }
  [DllImport("user32.dll")] static extern IntPtr SetWindowsHookEx(int id, LLP p, IntPtr h, uint t);
  [DllImport("user32.dll")] static extern IntPtr CallNextHookEx(IntPtr h, int c, IntPtr w, IntPtr l);
  [DllImport("kernel32.dll")] static extern IntPtr GetModuleHandle(string n);
  static LLP keep; static IntPtr hk;
  static IntPtr Hook(int c, IntPtr w, IntPtr l) {
    if (c >= 0) { MS m = (MS)Marshal.PtrToStructure(l, typeof(MS));
      ulong e = m.extra.ToUInt64(); int msg = w.ToInt32();
      if ((m.flags & 1) != 0) Inj++; else if ((e & 0xFFFFFF00) == 0xFF515700) TouchSig++; else Phys++;
      if (msg == 0x201 || msg == 0x204) Down++; if (msg == 0x202 || msg == 0x205) Up++; }
    return CallNextHookEx(hk, c, w, l); }
  [StructLayout(LayoutKind.Sequential)] struct RID { public ushort page, usage; public uint flags; public IntPtr hwnd; }
  [DllImport("user32.dll")] static extern bool RegisterRawInputDevices(RID[] r, uint n, uint sz);
  [StructLayout(LayoutKind.Sequential)] struct RIH { public uint type, size; public IntPtr dev, w; }
  [DllImport("user32.dll")] static extern uint GetRawInputData(IntPtr h, uint cmd, IntPtr d, ref uint sz, uint hs);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern uint GetRawInputDeviceInfo(IntPtr d, uint cmd, System.Text.StringBuilder s, ref uint sz);
  [StructLayout(LayoutKind.Sequential)] public struct PDI { public uint orient; public IntPtr dev; public uint type; public IntPtr mon; public uint cursorId; public ushort maxCount; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=520)] public string name; }
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern bool GetPointerDevices(ref uint n, [Out] PDI[] d);
  public static string Pointers() { try { uint n = 0; GetPointerDevices(ref n, null); var a = new PDI[n]; GetPointerDevices(ref n, a);
      var s = new List<string>(); foreach (var p in a) s.Add("type=" + p.type + " mon=" + p.mon + "(" + MonName(p.mon) + ") contacts=" + p.maxCount + " name=" + p.name); return string.Join(" | ", s); } catch (Exception x) { return "err " + x.Message; } }
  class W : NativeWindow { public W() { CreateHandle(new CreateParams());
      var r = new RID[] { new RID{page=1,usage=2,flags=0x100,hwnd=Handle}, new RID{page=0xD,usage=4,flags=0x100,hwnd=Handle}, new RID{page=0xD,usage=5,flags=0x100,hwnd=Handle} };
      RegisterRawInputDevices(r, (uint)r.Length, (uint)Marshal.SizeOf(typeof(RID))); }
    protected override void WndProc(ref Message m) { if (m.Msg == 0xFF) { try {
        uint sz = 0; uint hs = (uint)Marshal.SizeOf(typeof(RIH)); GetRawInputData(m.LParam, 0x10000005, IntPtr.Zero, ref sz, hs);
        IntPtr b = Marshal.AllocHGlobal((int)sz); GetRawInputData(m.LParam, 0x10000005, b, ref sz, hs);
        RIH h = (RIH)Marshal.PtrToStructure(b, typeof(RIH)); Marshal.FreeHGlobal(b);
        string k = "type" + h.type; if (h.dev == IntPtr.Zero) k += ":NULL(injected)"; else { uint n = 512; var s = new System.Text.StringBuilder(512); GetRawInputDeviceInfo(h.dev, 0x20000007, s, ref n); k += ":" + s.ToString(); }
        lock (Raw) { int v; Raw.TryGetValue(k, out v); Raw[k] = v + 1; } } catch {} } base.WndProc(ref m); } }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct DD { public int cb; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string name; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string str; public uint flags; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string id; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string key; }
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern bool EnumDisplayDevices(string dev, uint i, ref DD d, uint f);
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct MIEX { public int cb; public int l,t,r,b, wl,wt,wr,wb; public uint flags; [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dev; }
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern bool GetMonitorInfo(IntPtr h, ref MIEX m);
  static string MonName(IntPtr h) { var m = new MIEX(); m.cb = Marshal.SizeOf(typeof(MIEX)); return GetMonitorInfo(h, ref m) ? m.dev : "?"; }
  public static string Displays() { var o = new List<string>(); try {
    for (uint i = 0; i < 32; i++) { var a = new DD(); a.cb = Marshal.SizeOf(typeof(DD)); if (!EnumDisplayDevices(null, i, ref a, 0)) break;
      if ((a.flags & 1) == 0) continue; // attached to desktop only
      string mons = ""; for (uint j = 0; j < 4; j++) { var m = new DD(); m.cb = Marshal.SizeOf(typeof(DD)); if (!EnumDisplayDevices(a.name, j, ref m, 0)) break; mons += "[" + m.str + " " + m.id + "]"; }
      o.Add(a.name + ((a.flags & 4) != 0 ? " PRIMARY" : "") + " adapter=" + a.str + " " + mons); } } catch (Exception x) { o.Add("err " + x.Message); }
    return string.Join(" | ", o); }
  static W win;
  public static void Start() { keep = Hook; hk = SetWindowsHookEx(14, keep, GetModuleHandle(null), 0); win = new W(); }
  public static string TakeRaw() { lock (Raw) { var s = new List<string>(); foreach (var kv in Raw) s.Add(kv.Value + "x " + kv.Key); Raw.Clear(); return string.Join(" ; ", s); } }
}
'@
function L($s) { $l = "$(Get-Date -Format 'HH:mm:ss') $s"; Add-Content $log $l; Write-Host $l }
function Conn {
  $p = Get-Process | Where-Object { $_.ProcessName -match 'adoluna|rustdesk' }
  $c = @(Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | Where-Object { $p.Id -contains $_.OwningProcess })
  $rf = Get-Item 'C:\ProgramData\AdolunaSupport\kiosk-remote' -ErrorAction SilentlyContinue
  $age = if ($rf) { [int]((Get-Date) - $rf.LastWriteTime).TotalSeconds } else { -1 }
  "procs=$(@($p).Count) tcp=$($c.Count) kioskRemoteAge=${age}s"
}
L "=== Adoluna touchdiag $ts user=$env:USERNAME host=$env:COMPUTERNAME"
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_0EEF|VID_29BD' -or $_.Class -match 'HIDClass|Mouse' } | ForEach-Object { L "pnp $($_.Status) $($_.Class) $($_.FriendlyName) $($_.InstanceId)" }
L "tabletinput: $(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Wisp\Pen\Digimon' -ErrorAction SilentlyContinue | Out-String -Width 400)"
try { [TD]::Start() } catch { L "START ERROR: $($_.Exception.Message)" }
function Digimon { try { $k = 'HKLM:\SOFTWARE\Microsoft\Wisp\Pen\Digimon'; if (Test-Path $k) { (Get-Item $k).Property | ForEach-Object { "$_ = $((Get-ItemProperty $k).$_)" } | Out-String -Width 500 } else { 'none' } } catch { "err $($_.Exception.Message)" } }
L "display devices (PnP): $((Get-PnpDevice -Class Display -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Status) $($_.FriendlyName) $($_.InstanceId)" }) -join ' / ')"
$sw = [Diagnostics.Stopwatch]::StartNew(); $next = 0; $script:n = 0; $prevTopo = ''
while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
  try { [Windows.Forms.Application]::DoEvents() } catch { L "PUMP ERROR: $($_.Exception.Message)" }
  if ($sw.ElapsedMilliseconds -ge $next) {
    $next += 1000; $script:n++
    try {
      $scr = ([Windows.Forms.Screen]::AllScreens | ForEach-Object { "$($_.DeviceName)$(if ($_.Primary) {'*'}) $($_.Bounds.Width)x$($_.Bounds.Height)@$($_.Bounds.X),$($_.Bounds.Y)" }) -join ' / '
      $line = "$(Conn) | screens=$scr | ll phys=$([TD]::Phys) inj=$([TD]::Inj) touchSig=$([TD]::TouchSig) down=$([TD]::Down) up=$([TD]::Up) | raw: $([TD]::TakeRaw())"
      [TD]::Phys = 0; [TD]::Inj = 0; [TD]::TouchSig = 0; [TD]::Down = 0; [TD]::Up = 0
      L $line
      $topo = [TD]::Displays()
      if ($topo -ne $prevTopo -or $script:n % 10 -eq 1) { L "topology: $topo"; L "pointerDevices: $([TD]::Pointers())"; L "digimon: $(Digimon)"; $prevTopo = $topo }
    } catch { L "SAMPLE ERROR: $($_.Exception.Message)" }
  }
  Start-Sleep -Milliseconds 20
}
# collect Adoluna logs
$z = Join-Path $out "touchdiag-$ts"; New-Item -ItemType Directory -Force $z | Out-Null
Copy-Item $log $z
foreach ($d in @("$env:APPDATA\AdolunaSupport\log", 'C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\AdolunaSupport\log', 'C:\Windows\System32\config\systemprofile\AppData\Roaming\AdolunaSupport\log')) {
  if (Test-Path $d) { Copy-Item $d (Join-Path $z ($d -replace '[\\:]', '_')) -Recurse -ErrorAction SilentlyContinue } }
Compress-Archive -Path "$z\*" -DestinationPath "$z.zip" -Force
L "DONE -> $z.zip"
