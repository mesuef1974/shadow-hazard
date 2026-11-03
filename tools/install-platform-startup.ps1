$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$runnerPath = Join-Path $projectRoot 'tools\run-platform.py'
if (-not (Test-Path -LiteralPath $pythonPath) -or -not (Test-Path -LiteralPath $runnerPath)) {
    throw 'Local platform environment or launcher is missing.'
}
$taskName = 'ShadowDanger-PlatformServer'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument ('"{0}"' -f $runnerPath) -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$recovery = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($trigger, $recovery) -Principal $principal -Settings $settings -Description 'Local Shadow Danger platform at http://127.0.0.1:8011; logon and 5-minute recovery; one OS-locked instance.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
