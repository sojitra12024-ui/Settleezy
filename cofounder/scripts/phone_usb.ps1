<#
  Setz on your phone over a USB cable (Samsung Galaxy S26 Ultra or any Android phone).

  The phone opens http://localhost:8765 and reaches the dashboard on this laptop through the cable
  (adb reverse). No Wi-Fi, no Tailscale, nothing exposed to the network. Chrome treats localhost as a
  secure address, so you can install Setz as an app, get notifications and talk to it with the phone mic.

  One-time setup on the phone:
    Settings > About phone > Software information > tap "Build number" 7 times (developer options)
    Settings > Developer options > USB debugging ON, then plug in and tap "Allow" on the phone.
  One-time setup on the laptop:
    winget install Google.PlatformTools        (adb)
    winget install Genymobile.scrcpy           (optional: mirror the phone on the laptop)

  Usage:
    .\scripts\phone_usb.ps1            connect once and open Setz on the phone
    .\scripts\phone_usb.ps1 -Watch     keep it connected: re-applies whenever you plug the phone in
    .\scripts\phone_usb.ps1 -Talk      open the push-to-talk page instead of the dashboard
    .\scripts\phone_usb.ps1 -Mirror    also mirror the phone screen on the laptop (scrcpy)
#>
param([switch]$Watch, [switch]$Talk, [switch]$Mirror, [int]$Port = 8765)
$ErrorActionPreference = "Stop"

function Find-Adb {
  $cmd = Get-Command adb -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  foreach ($p in @("$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe",
                   "$env:LOCALAPPDATA\Microsoft\WinGet\Links\adb.exe",
                   "C:\platform-tools\adb.exe")) { if (Test-Path $p) { return $p } }
  throw "adb not found. Install it with: winget install Google.PlatformTools (then open a new terminal)"
}

$adb = Find-Adb
$page = if ($Talk) { "/talk" } else { "/" }

function Connect-Phone {
  $devices = & $adb devices | Select-Object -Skip 1 | Where-Object { $_ -match "\S" }
  $ready = $devices | Where-Object { $_ -match "\tdevice$" }
  if (-not $ready) {
    if ($devices -match "unauthorized") { Write-Host "Unlock the phone and tap 'Allow USB debugging'." -ForegroundColor Yellow }
    return $false
  }
  & $adb reverse "tcp:$Port" "tcp:$Port" | Out-Null
  if ($LASTEXITCODE) { throw "adb reverse failed" }
  return $true
}

if (-not (Connect-Phone)) {
  if (-not $Watch) { throw "No phone found. Plug it in with USB debugging enabled (see the top of this script)." }
}
else {
  & $adb shell am start -a android.intent.action.VIEW -d "http://localhost:$Port$page" | Out-Null
  Write-Host "Setz is open on your phone: http://localhost:$Port$page" -ForegroundColor Green
  Write-Host "Tip: in Chrome tap the menu > 'Add to home screen' (Install app) once." -ForegroundColor DarkGray
  if ($Mirror) {
    $scrcpy = Get-Command scrcpy -ErrorAction SilentlyContinue
    if ($scrcpy) { Start-Process $scrcpy.Source -ArgumentList "--stay-awake --window-title Setz-Phone" } else { Write-Host "scrcpy not installed (winget install Genymobile.scrcpy)" }
  }
}

if ($Watch) {
  Write-Host "Watching for the phone (Ctrl+C to stop)..."
  $was = $false
  while ($true) {
    try { $now = Connect-Phone } catch { $now = $false }
    if ($now -and -not $was) { Write-Host "$(Get-Date -Format HH:mm) phone connected: http://localhost:$Port" -ForegroundColor Green }
    $was = $now
    Start-Sleep -Seconds 5
  }
}
