<#
  ForgeAgent installer for Windows 11.
    .\scripts\install.ps1                      # backend venv + frontend build + Playwright browser
    .\scripts\install.ps1 -InstallLlama        # also download llama.cpp (CUDA 12.4 build) to C:\AI\llama.cpp
    .\scripts\install.ps1 -DownloadModel       # also download Huihui-Qwen3.8-27B Q4_K_M (~15.4 GB) to C:\AI\Models\Huihui27B
#>
param(
  [switch]$InstallLlama,
  [switch]$DownloadModel,
  [string]$LlamaDir = "C:\AI\llama.cpp",
  [string]$ModelDir = "C:\AI\Models\Huihui27B",
  [string]$LlamaTag = ""            # empty = latest release
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Need($cmd, $hint) { if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd not found. $hint" } }

Step "Checking prerequisites"
Need python "Install Python 3.12+ from https://www.python.org/downloads/ (tick 'Add to PATH')."
Need node   "Install Node.js 20+ from https://nodejs.org/"
Need git    "Install Git for Windows from https://git-scm.com/download/win (also provides the bash shell the agent uses)."
$pyv = [version](python -c "import platform;print(platform.python_version())")
if ($pyv -lt [version]"3.12") { throw "Python 3.12+ required, found $pyv" }
Write-Host "python $pyv, node $(node --version), $(git --version)"
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) { Write-Warning "nvidia-smi not found: GPU acceleration needs an NVIDIA driver. The CPU build of llama.cpp still works (slowly)." }

Step "Backend: virtualenv + dependencies"
Push-Location "$Root\backend"
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install --upgrade pip | Out-Null
.\.venv\Scripts\python -m pip install -r requirements.txt
Step "Playwright Chromium (browser automation)"
.\.venv\Scripts\python -m playwright install chromium
Pop-Location

Step "Frontend: install + production build"
Push-Location "$Root\frontend"
npm install
npm run build
Pop-Location

if (-not (Test-Path "$Root\.env")) { Copy-Item "$Root\.env.example" "$Root\.env"; Write-Host "Created .env from .env.example" }

if ($InstallLlama) {
  Step "llama.cpp (CUDA 12.4 build) -> $LlamaDir"
  $api = if ($LlamaTag) { "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$LlamaTag" } else { "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" }
  $rel = Invoke-RestMethod $api
  New-Item -ItemType Directory -Force $LlamaDir | Out-Null
  foreach ($pat in '^llama-.*-bin-win-cuda-12\.4-x64\.zip$', '^cudart-llama-bin-win-cuda-12\.4-x64\.zip$') {
    $a = $rel.assets | Where-Object { $_.name -match $pat } | Select-Object -First 1
    if (-not $a) { throw "Release asset matching $pat not found in $($rel.tag_name); see docs/INSTALL.md for manual download." }
    $zip = Join-Path $env:TEMP $a.name
    Write-Host "Downloading $($a.name) ($([int]($a.size/1MB)) MB)"
    curl.exe -L --fail -o $zip $a.browser_download_url
    Expand-Archive $zip -DestinationPath $LlamaDir -Force; Remove-Item $zip
  }
  & "$LlamaDir\llama-server.exe" --version
}

if ($DownloadModel) {
  Step "Model download -> $ModelDir"
  python -m pip install -U huggingface_hub
  New-Item -ItemType Directory -Force $ModelDir | Out-Null
  hf download huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF --include "*Q4_K_M.gguf" --local-dir $ModelDir
}

Step "Done"
Write-Host "Start everything with:  .\scripts\start.ps1 -WithLlama"
