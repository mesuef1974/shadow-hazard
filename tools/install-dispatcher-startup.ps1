$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$consolePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$managePath = Join-Path $projectRoot 'backend\manage.py'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Project Python environment missing.' }
$arguments = '"{0}"' -f (Join-Path $projectRoot 'tools\run-dispatcher.py')
$taskName = 'ShadowDanger-ModelDispatcher'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$recoveryTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($trigger, $recoveryTrigger) -Principal $principal -Settings $settings -Description 'Shadow Danger worker: start at user logon; recover every 5 minutes in the same interactive session; one OS-locked instance.' -Force | Out-Null
& $consolePython $managePath configure_local_dispatcher --startup-method 'Windows — عند تسجيل الدخول'
if ($LASTEXITCODE -ne 0) { throw 'Unable to record startup configuration.' }
Start-ScheduledTask -TaskName $taskName
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
