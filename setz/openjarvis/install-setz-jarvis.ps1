<#
.SYNOPSIS
    Installs OpenJarvis on this Windows laptop and sets it up as Setz.

.DESCRIPTION
    Run from a git clone of the Settleezy repository:
        powershell -ExecutionPolicy Bypass -File setz\openjarvis\install-setz-jarvis.ps1

    1. Installs OpenJarvis with its official installer (uv, Python env, Ollama, a starter local model).
    2. Chooses the brain: Claude (needs an Anthropic API key, billed separately from your Claude plan)
       or a free local model.
    3. Applies the Setz system prompt and the Setz skills (council, decision, radar, briefings).
    4. Creates the access key that protects the app. Shown once and copied to the clipboard.
    5. Builds the web app, starts OpenJarvis at every logon, adds a desktop shortcut.
    6. Registers a daily update at 06:30 (OpenJarvis, its skills, and Setz from this repository).
    7. Optional: Tailscale, so your phone reaches the laptop privately. Nothing is opened to the internet.

    Keys are never written into this repository or printed into logs.

.PARAMETER Brain
    claude (default) or local.

.PARAMETER SkipPhone
    Do not install or configure Tailscale.
#>
[CmdletBinding()]
param(
    [ValidateSet('claude', 'local')] [string] $Brain = 'claude',
    [switch] $SkipPhone
)

$ErrorActionPreference = 'Stop'
function Step($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "    $t" -ForegroundColor Green }
function Warn($t) { Write-Host "    $t" -ForegroundColor Yellow }

if ($env:OS -ne 'Windows_NT') { throw 'This script is for Windows.' }

$Here = $PSScriptRoot
$Repo = (Resolve-Path (Join-Path $Here '..\..')).Path
if (-not (Test-Path (Join-Path $Repo '.claude\skills\setz-council\SKILL.md'))) {
    throw "Run this script from inside the Settleezy repository (setz\openjarvis\). Not found: $Repo\.claude\skills"
}
$Root    = if ($env:OPENJARVIS_HOME) { $env:OPENJARVIS_HOME } else { Join-Path $env:LOCALAPPDATA 'OpenJarvis' }
$Src     = Join-Path $Root 'src'
$Jarvis  = Join-Path $Root 'bin\jarvis.cmd'
$CfgDir  = Join-Path $env:USERPROFILE '.openjarvis'
$Cfg     = Join-Path $CfgDir 'config.toml'
$SetzDir = Join-Path $Root 'setz'
$Port    = 8000

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Read-ApiKeyFromConfig {
    if (-not (Test-Path $Cfg)) { return '' }
    $m = Select-String -Path $Cfg -Pattern '^\s*api_key\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value } else { return '' }
}

# ---------------------------------------------------------------- 1. OpenJarvis
Step 'Installing OpenJarvis (official installer, latest version)'
$installer = Join-Path $env:TEMP 'openjarvis-install.ps1'
Invoke-RestMethod 'https://open-jarvis.github.io/OpenJarvis/install.ps1' -OutFile $installer
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -SkipService
if ($LASTEXITCODE -ne 0) { throw "The OpenJarvis installer failed (exit $LASTEXITCODE). See the output above." }
Refresh-Path
if (-not (Test-Path $Jarvis)) { throw "OpenJarvis did not install where expected: $Jarvis" }
Ok "Installed in $Root"

if (-not (Test-Path $Cfg)) {
    & $Jarvis init --preset chat-simple --force | Out-Null
}

# ---------------------------------------------------------------- 2. Brain
Step "Brain: $Brain"
if ($Brain -eq 'claude') {
    if (-not [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', 'User')) {
        Write-Host '    Paste your Anthropic API key (console.anthropic.com > API keys). It is stored only in your Windows user profile.'
        $sec = Read-Host -AsSecureString '    Anthropic API key'
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
        if (-not $plain) { throw 'No key entered. Re-run with -Brain local to use the free local model instead.' }
        [Environment]::SetEnvironmentVariable('ANTHROPIC_API_KEY', $plain, 'User')
        $env:ANTHROPIC_API_KEY = $plain
        $plain = $null
        Ok 'Anthropic key saved to your user environment.'
    } else { Ok 'Anthropic key already set.' }
} else {
    Ok 'Using the local model installed by OpenJarvis (free, offline, less capable).'
}

# ---------------------------------------------------------------- 3. Setz prompt and skills
Step 'Applying the Setz prompt and skills'
& uv run --project $Src python (Join-Path $Here 'configure_setz.py') --brain $Brain
if ($LASTEXITCODE -ne 0) { throw 'Setz configuration failed.' }

# ---------------------------------------------------------------- 4. Access key
Step 'Access key (your login for the app on laptop and phone)'
$key = Read-ApiKeyFromConfig
if (-not $key) { & $Jarvis auth create-key | Out-Null; $key = Read-ApiKeyFromConfig }
if (-not $key) { throw 'Could not create the access key.' }
try { Set-Clipboard -Value $key; Ok 'Copied to the clipboard. Save it in your password manager now.' } catch { Warn 'Could not copy to the clipboard.' }
Write-Host "    Access key: $key" -ForegroundColor White
Write-Host '    Anyone with this key and network access to the laptop can use Setz. To replace it: jarvis auth revoke-key, then jarvis auth create-key.'
$key = $null

# ---------------------------------------------------------------- 5. Web app, autostart, shortcut
Step 'Building the web app'
Refresh-Path
$node = Get-Command node -ErrorAction SilentlyContinue
$needNode = $true
if ($node) { $v = [version]((& node --version).TrimStart('v')); $needNode = ($v -lt [version]'22.22.0') }
if ($needNode) {
    winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements --silent
    Refresh-Path
}
Push-Location (Join-Path $Src 'frontend')
try {
    & npx -y npm@11 ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    & npx -y npm@11 run build
    if ($LASTEXITCODE -ne 0) { throw 'web app build failed' }
} finally { Pop-Location }
Ok 'Web app built.'

Step 'Starting OpenJarvis at every logon (this laptop only, port 8000)'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Src 'deploy\windows\jarvis-service.ps1') install -ListenPort $Port
Start-ScheduledTask -TaskName 'OpenJarvis' -ErrorAction SilentlyContinue
$url = "http://127.0.0.1:$Port/"
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Setz Jarvis.lnk'))
$lnk.TargetPath = $url; $lnk.Save()
Ok "Desktop shortcut 'Setz Jarvis' opens $url"

# ---------------------------------------------------------------- 6. Daily update
Step 'Daily update at 06:30'
New-Item -ItemType Directory -Force -Path $SetzDir | Out-Null
Copy-Item (Join-Path $Here 'update-setz-jarvis.ps1') $SetzDir -Force
Set-Content -Path (Join-Path $SetzDir 'repo.txt') -Value $Repo -Encoding UTF8
Set-Content -Path (Join-Path $SetzDir 'brain.txt') -Value $Brain -Encoding UTF8
$act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$SetzDir\update-setz-jarvis.ps1`""
$trg = New-ScheduledTaskTrigger -Daily -At 6:30am
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'Setz Jarvis daily update' -Action $act -Trigger $trg -Settings $set -Description 'Updates OpenJarvis, its skills and Setz; log in setz\update.log' -Force | Out-Null
Ok "Registered. Log: $SetzDir\update.log"

# ---------------------------------------------------------------- 7. Phone
if (-not $SkipPhone) {
    Step 'Phone access through Tailscale (private network, nothing opened to the internet)'
    $ts = Get-Command tailscale -ErrorAction SilentlyContinue
    if (-not $ts) {
        winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements --silent
        Refresh-Path
        $ts = Get-Command tailscale -ErrorAction SilentlyContinue
    }
    if (-not $ts) {
        Warn 'Tailscale is not on PATH yet. Open Tailscale from the Start menu, sign in, then run: tailscale serve --bg 8000'
    } else {
        $st = $null
        try { $st = (& tailscale status --json | ConvertFrom-Json) } catch {}
        if (-not $st -or $st.BackendState -ne 'Running') {
            Warn 'Sign in to Tailscale (it opens in your browser), then press Enter.'
            & tailscale up
            Read-Host '    Press Enter when signed in' | Out-Null
            try { $st = (& tailscale status --json | ConvertFrom-Json) } catch {}
        }
        & tailscale serve --bg $Port
        if ($st -and $st.Self.DNSName) {
            $phone = 'https://' + $st.Self.DNSName.TrimEnd('.') + '/'
            Ok "On your phone, open: $phone"
        } else {
            Warn 'Run "tailscale serve status" to see the phone address.'
        }
    }
}

Step 'Done'
Write-Host '    Laptop: double-click "Setz Jarvis" on the desktop. Open Settings, paste the access key, choose "Continue without sharing".'
if (-not $SkipPhone) { Write-Host '    Phone: install Tailscale from the Play Store, sign in with the same account, open the address above, paste the key, then "Add to Home screen".' }
