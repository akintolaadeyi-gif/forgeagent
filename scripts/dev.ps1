<# Development mode: backend with auto-reload on :8000 and the Vite dev server (hot reload) on :5173. #>
$Root = Split-Path -Parent $PSScriptRoot
$py = "$Root\backend\.venv\Scripts\python.exe"
Start-Process $py -ArgumentList @('-m','uvicorn','app.main:app','--reload','--host','127.0.0.1','--port','8000') -WorkingDirectory "$Root\backend"
Push-Location "$Root\frontend"; npm run dev; Pop-Location
