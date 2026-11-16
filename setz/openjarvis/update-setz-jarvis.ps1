<#
.SYNOPSIS
    Daily update for Setz on OpenJarvis. Registered by install-setz-jarvis.ps1 to run at 06:30.

    1. Updates OpenJarvis to its latest release.
    2. Pulls the latest commits of installed skill sources.
    3. Pulls the latest Setz instructions and skills from the Settleezy repository, if it is a git clone.
    4. Re-applies the Setz configuration and rebuilds the web app.
    5. Restarts OpenJarvis.
    Every step is logged; a failed step does not stop the others.
#>
$ErrorActionPreference = 'Continue'
$Root    = if ($env:OPENJARVIS_HOME) { $env:OPENJARVIS_HOME } else { Join-Path $env:LOCALAPPDATA 'OpenJarvis' }
$Src     = Join-Path $Root 'src'
$Jarvis  = Join-Path $Root 'bin\jarvis.cmd'
$SetzDir = Join-Path $Root 'setz'
$Log     = Join-Path $SetzDir 'update.log'
$Repo    = (Get-Content (Join-Path $SetzDir 'repo.txt') -ErrorAction SilentlyContinue | Select-Object -First 1)
$Brain   = (Get-Content (Join-Path $SetzDir 'brain.txt') -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $Brain) { $Brain = 'claude' }
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')

function Log($t) { Add-Content -Path $Log -Value ("{0:yyyy-MM-dd HH:mm} {1}" -f (Get-Date), $t) }
function Run($name, [scriptblock] $b) {
    $global:LASTEXITCODE = 0
    try { $out = & $b 2>&1 | Out-String; if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE`n$out" }; Log "OK     $name" }
    catch { Log "FAILED $name : $($_.Exception.Message -replace '\s+', ' ')" }
}

Log '--- daily update start'
Run 'OpenJarvis self-update'      { & $Jarvis self-update }
Run 'skill sources update'        { & $Jarvis skill update }
if ($Repo -and (Test-Path (Join-Path $Repo '.git'))) {
    Run 'Setz repository pull'    { & git -C $Repo pull --ff-only }
} else {
    Log 'SKIP   Setz repository pull (not a git clone; Setz skills stay as installed)'
}
if ($Repo) {
    Run 'Setz configuration'      { & uv run --project $Src python (Join-Path $Repo 'setz\openjarvis\configure_setz.py') --brain $Brain }
}
Run 'web app rebuild'             { Push-Location (Join-Path $Src 'frontend'); try { & npx -y npm@11 ci --no-audit --no-fund; if ($LASTEXITCODE -eq 0) { & npx -y npm@11 run build } } finally { Pop-Location } }
Run 'restart OpenJarvis'          { Stop-ScheduledTask -TaskName 'OpenJarvis' -ErrorAction SilentlyContinue; Start-Sleep 3; Start-ScheduledTask -TaskName 'OpenJarvis' }
Log '--- daily update end'
