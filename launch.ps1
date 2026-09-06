$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$watch = Join-Path $root 'watch.bat'
$marker = Split-Path -Leaf $root
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
  $cl = $_.CommandLine
  $ex = $_.ExecutablePath
  if (($cl -and ($cl -like ('*' + $marker + '*'))) -or ($ex -and ($ex -like ('*' + $marker + '*'))) -or ($cl -and ($cl -like '*bot.py*'))) {
    if ($cl -like '*bot.py*' -or $ex -like ('*' + $marker + '*')) {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
}
Start-Sleep -Milliseconds 300
$arg = '/c start "uchetskup-bot" /MIN "' + $watch + '"'
Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" -ArgumentList $arg -WorkingDirectory $root
Start-Sleep -Seconds 4
$ok = $false
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
  if (($_.ExecutablePath -like '*motya-bot*') -or ($_.CommandLine -like '*motya-bot*')) {
    Write-Output ("running pid=" + $_.ProcessId)
    $ok = $true
  }
}
if (-not $ok) { Write-Output 'STILL_DOWN' }
