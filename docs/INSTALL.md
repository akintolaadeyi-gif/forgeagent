# Installation guide (Windows 11)

## 1. Prerequisites

| Requirement | Why | Get it |
|---|---|---|
| Python 3.12+ | backend | python.org (tick *Add to PATH*) |
| Node.js 20+ | build the UI, and for projects the agent builds | nodejs.org |
| Git for Windows | checkpoints, patches, and the **bash shell** the agent's `run_command` uses | git-scm.com |
| NVIDIA driver (>= 550 for CUDA 12.4) | GPU inference | nvidia.com/drivers |
| ~20 GB free disk | model (15.4 GB) + llama.cpp + dependencies | |

Optional: WSL2 / Docker are **not** used by ForgeAgent (see Limitations in ARCHITECTURE.md).

## 2. Automatic install

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd ForgeAgent
.\scripts\install.ps1 -InstallLlama -DownloadModel
```

This creates `backend\.venv`, installs Python dependencies, installs the Playwright Chromium build, runs `npm install && npm run build`,
creates `.env`, downloads the CUDA 12.4 build of llama.cpp to `C:\AI\llama.cpp` and the model to `C:\AI\Models\Huihui27B`.

## 3. Manual model download (what the installer runs)

```powershell
pip install -U huggingface_hub
hf download huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF --include "*Q4_K_M.gguf" --local-dir "C:\AI\Models\Huihui27B"
```

The pattern matches one file: `Huihui-Qwen3.8-27B-abliterated-UD-DW-Q4_K_M.gguf` (15.4 GiB).

**Recommended on a 16 GB GPU: also download IQ3_S** (`--include "*UD-IQ3_S.gguf"`, 11.1 GB). It fits entirely in VRAM and generated about 5x faster than
Q4_K_M in our measurements (36 vs 7 tokens/s; see `docs/MODEL_COMPARISON.md`) with no quality difference detected on a small test. Keep Q4_K_M for hard tasks.
Select the model in *Settings -> Model & server* or pass `-Model` to `start-llama.ps1`. `IQ4_XS` (13.4 GB, `*UD-IQ4_XS.gguf`) sits in between.

## 4. Run

```powershell
.\scripts\start.ps1 -WithLlama          # llama-server (127.0.0.1:8080) + backend (127.0.0.1:8000) + browser
.\scripts\stop.ps1
```

* The model takes ~30-90 s to load; the bottom bar of the UI shows a green dot once the server answers.
* You can also run llama-server yourself (`.\scripts\start-llama.ps1`) or point *Settings -> API endpoint* at any OpenAI-compatible server.
* Context: 16 384 by default. Try `-Context 32768` after checking VRAM/RAM (watch the bottom bar). The context in *Settings* must match
  (not exceed) what llama-server was started with.

### Fitting a 27B Q4 model on a 16 GB GPU

llama.cpp's automatic fitting (`-ngl` omitted, which is the script's default) offloads as many layers as free VRAM allows and keeps the rest
in system RAM. With 16 GB this means partial CPU offload; the KV cache is quantized to q8_0 to save memory. To tune manually:
`.\scripts\start-llama.ps1 -GpuLayers 48 -KvCache q4_0`. If you get out-of-memory errors lower `-GpuLayers` or the context.

## 5. First project

1. Open http://127.0.0.1:8000 -> *Projects* -> *New project* (pick or create an empty folder; it becomes the agent's only writable area).
2. In the workspace type a request, e.g. *"Create a Flask todo API with SQLite and pytest tests, run the tests."* Leave **Agent** mode and
   *Plan: auto*.
3. Watch *Activity*; approve/deny prompts in the chat panel; use **Stop** or **Pause** any time. Interrupted tasks show **Resume** in *Tasks*.

## 6. Development

```powershell
.\scripts\dev.ps1            # backend --reload on :8000 + Vite on :5173 (proxying /api)
cd backend; .\.venv\Scripts\python -m pytest
cd frontend; npm run typecheck
```

Database migrations are Alembic (`backend\alembic`); they are applied automatically at startup.
