# adoluna-b82 basic kiosk loop (runs in the signed-in user's session; written by Adoluna Support)
$ErrorActionPreference = 'SilentlyContinue'
$App = '{{APP}}'; $Name = '{{NAME}}'; $Own = '{{OWN}}'
$D = Join-Path $env:ProgramData 'AdolunaSupport'
# replace an older loop (e.g. the b81 one) that may still be running in this session
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like '*AdolunaSupport\kiosk.ps1*' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -Name W -Namespace A -MemberDefinition @'
[DllImport("user32.dll")] public static extern IntPtr FindWindow(string c, string t);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
[DllImport("user32.dll")] public static extern void SwitchToThisWindow(IntPtr h, bool a);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
'@
# b87: low-level keyboard hook in this (user) session, active only while locked. Blocks the shell
# shortcuts immediately (the scancode map / NoWinKeys policies only apply after the next sign-in).
# Injected keys (our own remote session) are never blocked.
Add-Type -Language CSharp -TypeDefinition @'
using System; using System.Runtime.InteropServices; using System.Diagnostics;
namespace A {
public static class KH {
  public static volatile bool Active = false;
  delegate IntPtr P(int n, IntPtr w, IntPtr l);
  static P keep; static IntPtr h = IntPtr.Zero;
  [DllImport("user32.dll")] static extern IntPtr SetWindowsHookEx(int id, P cb, IntPtr mod, uint tid);
  [DllImport("user32.dll")] static extern IntPtr CallNextHookEx(IntPtr h, int n, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] static extern short GetAsyncKeyState(int k);
  [DllImport("kernel32.dll")] static extern IntPtr GetModuleHandle(string m);
  [StructLayout(LayoutKind.Sequential)] struct K { public uint vk, sc, flags, time; public IntPtr extra; }
  static bool Down(int k) { return (GetAsyncKeyState(k) & 0x8000) != 0; }
  public static void Install() {
    if (h != IntPtr.Zero) return;
    keep = Cb;
    h = SetWindowsHookEx(13, keep, GetModuleHandle(Process.GetCurrentProcess().MainModule.ModuleName), 0);
  }
  static IntPtr Cb(int n, IntPtr w, IntPtr l) {
    try {
      if (n >= 0 && Active) {
        K k = (K)Marshal.PtrToStructure(l, typeof(K));
        bool injected = (k.flags & 0x10) != 0;
        if (!injected) {
          uint v = k.vk; bool alt = (k.flags & 0x20) != 0; bool ctrl = Down(0x11);
          bool block =
            v == 0x5B || v == 0x5C || v == 0x5D ||            // Win L/R, Apps (menu)
            v == 0x7A || v == 0x7B ||                         // F11, F12
            (alt && (v == 0x09 || v == 0x1B || v == 0x73 || v == 0x20)) || // Alt+Tab/Esc/F4/Space
            (ctrl && v == 0x1B) ||                            // Ctrl+Esc, Ctrl+Shift+Esc
            v == 0xA6 || v == 0xA7 || v == 0xAA || v == 0xAC || v == 0xB4 || v == 0xB5 || v == 0xB6 || v == 0xB7; // browser/launch keys
          if (block) return (IntPtr)1;
        }
      }
    } catch { }
    return CallNextHookEx(h, n, w, l);
  }
}}
'@
[A.KH]::Install()
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$zh = [Globalization.CultureInfo]::CurrentUICulture.Name -like 'zh*'
function L($c, $e) { if ($zh) { $c } else { $e } }
function Now { [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
# The pause is only trusted when the SYSTEM service wrote it during this boot (a reboot always relocks).
function PauseUntil {
  $f = Join-Path $D 'kiosk-pause'
  if (-not (Test-Path $f)) { return 0 }
  $i = Get-Item $f
  if ($i.LastWriteTime -lt $boot) { return 0 }
  $o = (Get-Acl $f).Owner
  if ($o -notmatch 'SYSTEM|Administrators') { return 0 }
  $v = 0; [long]::TryParse(((Get-Content $f -Raw) -split '\|')[0].Trim(), [ref]$v) | Out-Null; return $v
}
function Taskbar($s) { $cs = @('Shell_TrayWnd', 'Shell_SecondaryTrayWnd'); if ($s -eq 0) { $cs += 'Button' }; foreach ($c in $cs) { $h = [A.W]::FindWindow($c, $null); if ($h -ne [IntPtr]::Zero) { [A.W]::ShowWindow($h, $s) | Out-Null } } }
function Req($n, $v) { New-Item -ItemType Directory -Force (Join-Path $D 'kiosk-req') | Out-Null; [IO.File]::WriteAllText((Join-Path $D "kiosk-req\$n"), $v) }

$script:dlg = $false
function CodeDialog {
  $script:dlg = $true
  $f = New-Object Windows.Forms.Form
  $f.Text = 'Adoluna Support'; $f.TopMost = $true; $f.StartPosition = 'CenterScreen'; $f.FormBorderStyle = 'FixedDialog'
  $f.MinimizeBox = $false; $f.MaximizeBox = $false; $f.ClientSize = New-Object Drawing.Size(380, 398); $f.Font = New-Object Drawing.Font('Microsoft YaHei UI', 11)
  $t = New-Object Windows.Forms.Label; $t.Text = (L '临时解锁 30 分钟：输入解锁码（6 位）或一次性码（8 位）' 'Unlock for 30 min: enter the unlock code (6 digits) or one-time code (8 digits)')
  $t.SetBounds(16, 12, 350, 50)
  $e = New-Object Windows.Forms.TextBox; $e.SetBounds(16, 66, 348, 30); $e.MaxLength = 8; $e.Font = New-Object Drawing.Font('Consolas', 16)
  # on-screen numeric keypad (touch input on the kiosk; physical keyboard still works)
  $keys = @('1','2','3','4','5','6','7','8','9', (L '退格' 'Back'), '0', (L '清除' 'Clear'))
  for ($ki = 0; $ki -lt 12; $ki++) {
    $kb = New-Object Windows.Forms.Button
    $kb.Text = $keys[$ki]; $kb.TabStop = $false
    $kb.Font = New-Object Drawing.Font('Microsoft YaHei UI', 14)
    $kb.SetBounds((16 + ($ki % 3) * 118), (104 + [Math]::Floor($ki / 3) * 52), 112, 44)
    if ($ki -eq 9) {
      $kb.Add_Click({ if ($e.Text.Length -gt 0) { $e.Text = $e.Text.Substring(0, $e.Text.Length - 1) }; $e.SelectionStart = $e.Text.Length; $e.Focus() })
    } elseif ($ki -eq 11) {
      $kb.Add_Click({ $e.Text = ''; $e.Focus() })
    } else {
      $kb.Add_Click({ if ($e.Text.Length -lt $e.MaxLength) { $e.AppendText($this.Text) }; $e.SelectionStart = $e.Text.Length; $e.Focus() })
    }
    $f.Controls.Add($kb)
  }
  $m = New-Object Windows.Forms.Label; $m.SetBounds(16, 312, 348, 26); $m.ForeColor = [Drawing.Color]::FromArgb(215, 38, 46)
  $ok = New-Object Windows.Forms.Button; $ok.Text = (L '解锁' 'Unlock'); $ok.SetBounds(196, 346, 80, 36)
  $cn = New-Object Windows.Forms.Button; $cn.Text = (L '取消' 'Cancel'); $cn.SetBounds(284, 346, 80, 36); $cn.DialogResult = 'Cancel'
  $f.AcceptButton = $ok; $f.CancelButton = $cn
  $ok.Add_Click({
    $code = $e.Text.Trim()
    if ($code -notmatch '^\d{6}$|^\d{8}$') { $m.Text = (L '请输入 6 位或 8 位数字' 'Enter 6 or 8 digits'); return }
    $nonce = [guid]::NewGuid().ToString('N'); $ok.Enabled = $false; $m.Text = (L '正在验证…' 'Checking…')
    Req 'unlock' "$nonce|$code"
    $r = ''
    for ($i = 0; $i -lt 40 -and -not $r; $i++) {
      [Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 150
      $l = Get-Content (Join-Path $D 'kiosk-result') -Raw
      if ($l -and $l.StartsWith("$nonce|")) { $r = $l.Substring($nonce.Length + 1).Trim() }
    }
    $ok.Enabled = $true; $e.Text = ''
    switch ($r) {
      'ok' { $f.DialogResult = 'OK'; $f.Close() }
      'bad' { $m.Text = (L '解锁码不正确' 'Wrong code') }
      'locked' { $m.Text = (L '错误次数过多，请 5 分钟后再试' 'Too many attempts, try again in 5 minutes') }
      default { $m.Text = (L 'Adoluna Support 服务未响应' 'Adoluna Support service did not respond') }
    }
  })
  $f.Controls.AddRange(@($t, $e, $m, $ok, $cn)); $f.Add_Shown({ $f.TopMost = $true; $f.Activate(); [A.W]::SetForegroundWindow($f.Handle) | Out-Null; $e.Focus() })
  $f.ShowDialog() | Out-Null; $f.Dispose(); $script:dlg = $false
}

# hidden hot corner: 7 taps within 5 s in the top-left corner (same as Android)
$hc = New-Object Windows.Forms.Form
$hc.FormBorderStyle = 'None'; $hc.ShowInTaskbar = $false; $hc.TopMost = $true; $hc.StartPosition = 'Manual'
$hc.Location = New-Object Drawing.Point(0, 0); $hc.Size = New-Object Drawing.Size(40, 40); $hc.Opacity = 0.01; $hc.BackColor = [Drawing.Color]::Black
$script:taps = @()
$hc.Add_MouseDown({
  $n = [DateTime]::UtcNow; $script:taps = @($script:taps | Where-Object { ($n - $_).TotalSeconds -lt 5 }) + $n
  if ($script:taps.Count -ge 7 -and -not $script:dlg) { $script:taps = @(); if ((PauseUntil) -le (Now)) { CodeDialog } }
})

# floating countdown (draggable; tap = lock now)
$fl = New-Object Windows.Forms.Form
$fl.FormBorderStyle = 'None'; $fl.ShowInTaskbar = $false; $fl.TopMost = $true; $fl.StartPosition = 'Manual'
$fl.BackColor = [Drawing.Color]::FromArgb(215, 38, 46); $fl.Size = New-Object Drawing.Size(230, 44)
$wa = [Windows.Forms.Screen]::PrimaryScreen.WorkingArea; $fl.Location = New-Object Drawing.Point(($wa.Right - 250), ($wa.Top + 80))
$lb = New-Object Windows.Forms.Label; $lb.Dock = 'Fill'; $lb.TextAlign = 'MiddleCenter'; $lb.ForeColor = [Drawing.Color]::White
$lb.Font = New-Object Drawing.Font('Microsoft YaHei UI', 11, [Drawing.FontStyle]::Bold); $lb.Cursor = 'Hand'
$fl.Controls.Add($lb)
$script:drag = $null; $script:moved = $false
$lb.Add_MouseDown({ $script:drag = [Windows.Forms.Cursor]::Position; $script:org = $fl.Location; $script:moved = $false })
$lb.Add_MouseMove({ if ($script:drag) { $p = [Windows.Forms.Cursor]::Position; $dx = $p.X - $script:drag.X; $dy = $p.Y - $script:drag.Y
  if ([Math]::Abs($dx) + [Math]::Abs($dy) -gt 6) { $script:moved = $true }
  if ($script:moved) { $fl.Location = New-Object Drawing.Point(($script:org.X + $dx), ($script:org.Y + $dy)) } } })
$lb.Add_MouseUp({ $script:drag = $null; if (-not $script:moved) { Req 'relock' "$(Now)"; $fl.Hide() } })

$script:tick = 0
$tm = New-Object Windows.Forms.Timer; $tm.Interval = 1000
$tm.Add_Tick({
  $script:tick++
  # pause the foreground watchdog while the unlock dialog is open so it can't steal focus
  if ($script:dlg) { [A.KH]::Active = $true; return }
  $u = PauseUntil; $n = Now
  [A.KH]::Active = -not ($u -gt $n)
  if ($u -gt $n) {
    Taskbar 5; $s = $u - $n
    $lb.Text = (L ('剩余 {0:00}:{1:00} · 锁定' -f [Math]::Floor($s / 60), ($s % 60)) ('{0:00}:{1:00} left · Lock' -f [Math]::Floor($s / 60), ($s % 60)))
    if (-not $fl.Visible) { $fl.Show() }; $fl.TopMost = $true
    return
  }
  if ($fl.Visible) { $fl.Hide() }
  Taskbar 0; $hc.TopMost = $true
  $pos = Get-Process -Name $Name
  if (-not $pos) { if ($script:tick % 3 -eq 1) { Start-Process -FilePath $App }; return }
  if ($script:dlg) { return }
  # a technician is connected: don't fight their windows
  $rf = Join-Path $D 'kiosk-remote'
  if ((Test-Path $rf) -and ((Get-Date) - (Get-Item $rf).LastWriteTime).TotalSeconds -lt 10) { return }
  $fg = [A.W]::GetForegroundWindow(); $fp = 0; [A.W]::GetWindowThreadProcessId($fg, [ref]$fp) | Out-Null
  $fn = (Get-Process -Id $fp).ProcessName
  if ($fn -eq $Name) { return }
  # b87: Start / search / Task View / Action Center that slipped through: dismiss them
  if (@('StartMenuExperienceHost', 'SearchHost', 'SearchApp', 'ShellExperienceHost', 'SearchUI') -contains $fn) { [System.Windows.Forms.SendKeys]::SendWait('{ESC}') }
  if ($fg -ne [IntPtr]::Zero -and $fn -and @($Own, 'explorer', 'powershell', 'LockApp', 'consent', 'LogonUI') -notcontains $fn) { [A.W]::ShowWindow($fg, 6) | Out-Null }
  $w = ($pos | Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero } | Select-Object -First 1).MainWindowHandle
  if ($w) { if ([A.W]::IsIconic($w)) { [A.W]::ShowWindow($w, 9) | Out-Null }; [A.W]::SwitchToThisWindow($w, $true) }
})
$tm.Start()
$hc.Show()
[Windows.Forms.Application]::Run($hc)
