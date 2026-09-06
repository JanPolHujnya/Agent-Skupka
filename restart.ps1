$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$embedded = Join-Path $root 'runtime\python.exe'
$py = if (Test-Path $embedded) { $embedded } else { 'python' }
$bot = Join-Path $root 'bot.py'
$marker = Split-Path -Leaf $root
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
  $cl = $_.CommandLine
  $ex = $_.ExecutablePath
  if (($cl -like '*bot.py*') -and (($cl -like ('*' + $marker + '*')) -or ($ex -like ('*' + $marker + '*')))) {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
}
Start-Sleep -Milliseconds 400
Start-Process -FilePath $py -ArgumentList "`"$bot`"" -WorkingDirectory $root -WindowStyle Minimized
Write-Output 'restarted'
