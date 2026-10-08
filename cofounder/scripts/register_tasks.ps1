# Schedules the co-founder's routines in Windows Task Scheduler (runs as you, only while you're logged in).
# Re-run any time to update. Remove with:  Get-ScheduledTask -TaskPath "\Settleezy\" | Unregister-ScheduledTask -Confirm:$false
#   .\scripts\register_tasks.ps1            # default schedule
#   .\scripts\register_tasks.ps1 -Speak     # also read the morning brief aloud
#   .\scripts\register_tasks.ps1 -Voice     # also start "Hey Jarvis" at logon
param([switch]$Speak, [switch]$Voice)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$sz = Join-Path $root ".venv\Scripts\sz.exe"
if (-not (Test-Path $sz)) { throw "Run .\scripts\install.ps1 first." }
$log = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $log | Out-Null

function Add-SzTask($name, $arguments, $trigger, [switch]$Hidden) {
    $cmd = "/c `"`"$sz`" $arguments >> `"$log\$name.log`" 2>&1`""
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmd -WorkingDirectory $root
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
    if ($Hidden) { $settings.Hidden = $true }
    Register-ScheduledTask -TaskPath "\Settleezy\" -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host "scheduled: $name"
}

$weekdays = "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
$morningArgs = if ($Speak) { "morning --speak" } else { "morning" }

Add-SzTask "morning-brief"   $morningArgs      (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 7:45am)
$t = New-ScheduledTaskTrigger -Daily -At 9:00am
$t.Repetition = (New-ScheduledTaskTrigger -Once -At 9:00am -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Hours 10)).Repetition
Add-SzTask "mail-sync"       "run mail"        $t -Hidden
$t2 = New-ScheduledTaskTrigger -Daily -At 8:15am
$t2.Repetition = (New-ScheduledTaskTrigger -Once -At 8:15am -RepetitionInterval (New-TimeSpan -Hours 6) -RepetitionDuration (New-TimeSpan -Hours 13)).Repetition
Add-SzTask "competitor-scan" "run scrape"      $t2 -Hidden
Add-SzTask "lead-enrich"     "run enrich"      (New-ScheduledTaskTrigger -Daily -At 1:00pm) -Hidden
Add-SzTask "instagram-sync"  "run instagram"   (New-ScheduledTaskTrigger -Daily -At 6:00pm) -Hidden
Add-SzTask "afternoon-drafts" "run drafts"     (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 2:30pm) -Hidden
Add-SzTask "relearn-voice"   "run learn"       (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 8:00pm) -Hidden
Add-SzTask "growth-review"   "run growth"      (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 7:15am) -Hidden
Add-SzTask "dashboard"       "dashboard"       (New-ScheduledTaskTrigger -AtLogOn) -Hidden
if ($Voice) { Add-SzTask "voice" "voice" (New-ScheduledTaskTrigger -AtLogOn) -Hidden }

Write-Host "`nDashboard: http://127.0.0.1:8765  (starts at logon; run '.venv\Scripts\sz.exe dashboard' now to open it immediately)" -ForegroundColor Green
