<#
  Starts ForgeAgent (backend API + built UI) and optionally the llama.cpp server.
    .\scripts\start.ps1                  # backend only (attach to an already running llama-server)
    .\scripts\start.ps1 -WithLlama       # also launch llama-server (Q4_K_M, 16k context)
    .\scripts\start.ps1 -WithLlama -Context 32768 -GpuLayers 40
#>
param(
  [switch]$WithLlama,
  [int]$Context = 16384,
  [int]$GpuLayers = -1,
  [int]$Port = 8000,
  [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = "$Root\backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Backend not installed. Run .\scripts\install.ps1 first." }
if (-not (Test-Path "$Root\frontend\dist\index.html")) { Write-Warning "Frontend is not built (run install.ps1). The API will still work." }
New-Item -ItemType Directory -Force "$Root\data\logs" | Out-Null

function Port-Open($p) { try { (New-Object Net.Sockets.TcpClient).Connect("127.0.0.1", $p); $true } catch { $false } }
if (Port-Open $Port) { throw "Port $Port is already in use. Run .\scripts\stop.ps1 or choose -Port." }

if ($WithLlama) {
  if (Port-Open 8080) { Write-Host "llama-server already listening on 8080 - attaching." }
  else {
    Write-Host "Starting llama-server (first load of the 27B model takes ~30-90 s)..."
    $a = @('-NoProfile','-ExecutionPolicy','Bypass','-File',"$Root\scripts\start-llama.ps1",'-Context',$Context)
    if ($GpuLayers -ge 0) { $a += @('-GpuLayers',$GpuLayers) }
    Start-Process powershell -ArgumentList $a -WindowStyle Hidden -RedirectStandardOutput "$Root\data\logs\llama.out.log" -RedirectStandardError "$Root\data\logs\llama.err.log"
  }
}

Write-Host "Starting ForgeAgent backend on http://127.0.0.1:$Port"
$p = Start-Process $py -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port',$Port) -WorkingDirectory "$Root\backend" `
     -WindowStyle Hidden -PassThru -RedirectStandardOutput "$Root\data\logs\backend.out.log" -RedirectStandardError "$Root\data\logs\backend.err.log"
$p.Id | Set-Content "$Root\data\backend.pid"
for ($i = 0; $i -lt 40; $i++) { if (Port-Open $Port) { break }; Start-Sleep -Milliseconds 500 }
if (-not (Port-Open $Port)) { throw "Backend did not start. See data\logs\backend.err.log" }
Write-Host "ForgeAgent is running:  http://127.0.0.1:$Port" -ForegroundColor Green
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
