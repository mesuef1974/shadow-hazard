# Local deploy task (ADR 0005). Run once from the deploy checkout, after this script reaches main.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$runnerPath = Join-Path $projectRoot 'scripts\deploy_local.py'
if (-not (Test-Path -LiteralPath $pythonPath) -or -not (Test-Path -LiteralPath $runnerPath)) {
    throw 'Local environment or deploy script is missing.'
}
$taskName = 'ShadowDanger-Deploy'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument ('"{0}"' -f $runnerPath) -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Deploy origin/main locally every 5 minutes, with rollback.' -Force | Out-Null
