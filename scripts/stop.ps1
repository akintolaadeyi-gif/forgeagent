<# Stops the ForgeAgent backend, any llama-server it started, and the dev servers the agent launched (child processes). #>
$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
$pidFile = "$Root\data\backend.pid"
if (Test-Path $pidFile) { $id = [int](Get-Content $pidFile); taskkill /PID $id /T /F | Out-Null; Remove-Item $pidFile }
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'uvicorn app.main:app' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }
Get-Process llama-server | Stop-Process -Force
Write-Host "ForgeAgent stopped."
