$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$runnerPath = Join-Path $projectRoot 'tools\run-local-model.py'
$lmsPath = Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe'
foreach ($path in @($pythonPath, $runnerPath, $lmsPath)) {
    if (-not (Test-Path -LiteralPath $path)) { throw 'Installed local model prerequisites are missing.' }
}
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskName = 'ShadowDanger-LocalModel'
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument ('"{0}"' -f $runnerPath) -WorkingDirectory $projectRoot
$logon = New-ScheduledTaskTrigger -AtLogOn -User $identity
$recovery = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logon, $recovery) -Principal $principal -Settings $settings -Description 'Native LM Studio and installed Qwen model at Windows logon; platform8011; local API1234; no Docker.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
