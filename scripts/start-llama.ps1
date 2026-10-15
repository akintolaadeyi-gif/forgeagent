<#
  Starts llama.cpp's OpenAI-compatible server bound to localhost.

  Examples:
    .\start-llama.ps1                                   # Q4_K_M, 16k context, auto GPU/CPU split
    .\start-llama.ps1 -Context 32768 -GpuLayers 40      # bigger context, explicit layer count
    .\start-llama.ps1 -Model C:\AI\Models\x\other.gguf
#>
param(
  [string]$Model = "",
  [int]$Context = 16384,
  [int]$GpuLayers = -1,          # -1 = let llama.cpp fit as many layers as VRAM allows
  [int]$Port = 8080,
  [string]$KvCache = "q8_0",     # f16 | q8_0 | q4_0  (q8_0 halves KV memory with negligible quality loss)
  [string]$Server = "C:\AI\llama.cpp\llama-server.exe",
  [string]$ModelsDir = "C:\AI\Models",
  [string]$ExtraArgs = ""
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $Server)) { throw "llama-server not found at $Server (see docs/INSTALL.md)" }
if (-not $Model) {
  $cand = Get-ChildItem $ModelsDir -Recurse -Filter *.gguf -ErrorAction SilentlyContinue |
          Where-Object { $_.Name -notmatch 'mmproj' } | Sort-Object { $_.Name -notmatch 'Q4_K_M' }, Length | Select-Object -First 1
  if (-not $cand) { throw "No .gguf model found under $ModelsDir" }
  $Model = $cand.FullName
}
Write-Host "Model:   $Model"
Write-Host "Context: $Context   KV cache: $KvCache   Port: $Port (127.0.0.1 only)"

$args = @("-m", $Model, "-c", $Context, "--host", "127.0.0.1", "--port", $Port, "--jinja", "-fa", "on")
if ($GpuLayers -ge 0) { $args += @("-ngl", $GpuLayers) }
if ($KvCache -ne "f16") { $args += @("-ctk", $KvCache, "-ctv", $KvCache) }
if ($ExtraArgs) { $args += $ExtraArgs.Split(" ") }
Write-Host "$Server $($args -join ' ')"
& $Server @args
