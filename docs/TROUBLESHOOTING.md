# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Bottom bar: "Backend offline" | The Python backend is not running. `.\scripts\start.ps1`; look at `data\logs\backend.err.log` and `data\logs\forge.log`. |
| Bottom bar: "Model server not reachable" | llama-server is not up (the 27B model needs 30-90 s to load) or the endpoint in *Settings* is wrong. `curl http://127.0.0.1:8080/v1/models`. Check `data\logs\llama.err.log`. |
| llama-server crashes with out-of-memory | Lower context (`-Context 8192`), reduce layers (`-GpuLayers 40`), use `-KvCache q4_0`, or use the IQ3_S model. Close other GPU apps. |
| Very slow generation (< 5 tok/s) | Too many layers on CPU. Free VRAM, lower context, try IQ3_S. Check `nvidia-smi`. |
| "Context overflow" warnings / empty replies | The context set in *Settings* is larger than llama-server's `-c`. Make them equal. The agent compacts automatically; reduce *Tool output shown to model* if needed. |
| Agent only talks, never acts | Model ignores tools. Check `ctx` footer shows "text tool-calls" (fallback mode) - that is fine; otherwise raise temperature slightly / lower to 0.3-0.6, shorten the request, or use *Plan: always*. The agent is nudged up to *“Continue” nudges* times. |
| Task shows BLOCKED "Step limit reached" | Raise *Max steps per task* in *Settings -> Autonomy* and press **Resume** in *Tasks*. |
| Task BLOCKED "Model unavailable" | The model server died/restarted. Start it again, then **Resume**. |
| Task BLOCKED "Waiting for approval" | A tool needs your decision - see the amber card in the chat panel. |
| "N consecutive failing tool calls" | Open *Tools*, click the failed rows for real output; fix the environment (missing interpreter, port in use) and **Resume**. |
| `bash: pytest: command not found` in tool output | Dependencies are not on PATH. The agent should create `.venv` and call `.venv\Scripts\python -m pytest`; tell it so, or install packages yourself. |
| Browser tools fail: "Could not launch Chromium" | `backend\.venv\Scripts\python -m playwright install chromium` |
| `run_command` uses a different shell than I expect | It uses Git Bash (bash syntax) when Git for Windows is installed, otherwise PowerShell. Override with env var `FORGE_SHELL`. |
| Windows Defender / AV slows or blocks commands | Exclude your workspace folders and `data\` from real-time scanning. |
| Port 8000 / 8080 in use | `.\scripts\start.ps1 -Port 8010`; for llama-server use `start-llama.ps1 -Port 8081` and update *Settings -> API endpoint*. |
| Restore button disabled | The agent is running; press **Stop** first. |
| Project shows "Workspace folder is missing" | The folder was moved/deleted. Recreate it or remove the project (files are never deleted by ForgeAgent). |
| Lost everything after reinstall | State is in `data\forge.db` - back it up. Workspaces are plain folders with Git history. |
| Database error at startup | Delete nothing: run `backend\.venv\Scripts\alembic upgrade head` from `backend\` to see the migration error; the app falls back to creating missing tables. |

## Useful files
* `data\logs\forge.log` - backend log (rotating) · `data\logs\<project>_<server>.log` - dev-server output
* `data\forge.db` - SQLite (tasks, messages, audit `tool_runs`, memory) · `data\screenshots\` - browser screenshots
* Everything the agent did: *Workspace -> Tools* (click a row for full output) or `GET /api/projects/{id}/tool-runs`

## Diagnostics
```powershell
curl http://127.0.0.1:8000/api/model/status          # connection, model, server context, tool support
curl -X POST http://127.0.0.1:8000/api/model/test    # one real generation + tokens/s
curl http://127.0.0.1:8000/api/system/resources      # CPU / RAM / VRAM
```
