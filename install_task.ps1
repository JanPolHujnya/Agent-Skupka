$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$watch = Join-Path $root 'watch.bat'
$task = 'UchetSkupBot'
$marker = Split-Path -Leaf $root
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
  if (($_.CommandLine -like '*bot.py*') -and (($_.ExecutablePath -like ('*' + $marker + '*')) -or ($_.CommandLine -like ('*' + $marker + '*')))) {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
}
Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument ('/c "' + $watch + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $task
Start-Sleep -Seconds 5
$ok = $false
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
  if (($_.ExecutablePath -like '*motya-bot*') -or ($_.CommandLine -like '*motya-bot*')) {
    Write-Output ("running pid=" + $_.ProcessId)
    $ok = $true
  }
}
if (-not $ok) { Write-Output 'STILL_DOWN' }
Get-ScheduledTask -TaskName $task | Select-Object TaskName, State | Format-List
