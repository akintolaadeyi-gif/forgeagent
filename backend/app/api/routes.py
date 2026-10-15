"""REST + WebSocket API."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
import psutil
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..agent import memory as mem
from ..agent.prompts import ROLE_TOOLS
from ..agent.runtime import RunConflict
from ..app_settings import AppSettings, load_settings, save_settings
from ..config import get_env
from ..db import get_session, session_scope
from ..events import bus
from ..llama_proc import llama
from ..llm.gateway import LLMError, LLMGateway
from ..models import (Checkpoint, Event, LLMUsage, MemoryItem, Message, Project, Task, TaskStatus, ToolRun)
from ..security import WorkspaceViolation, resolve_in_workspace, touches_protected
from ..state import orchestrator as orch
from ..tools import REGISTRY
from ..tools import gitutil
from ..tools.fs_tools import IGNORE_DIRS, atomic_write

router = APIRouter(prefix="/api")

FORBIDDEN_ROOTS = [Path(p).resolve() for p in filter(None, [
    os.environ.get("SystemRoot"), os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
    os.environ.get("ProgramData"), str(Path.home()), str(Path.home().parent)])]


def _iso(d: datetime | None) -> str | None:
    """ISO-8601 with an explicit offset. SQLite returns naive datetimes (all stored values are UTC); without the offset a browser
    in UTC+1 would read them as local time and show everything an hour old."""
    if not d:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.isoformat()


# ================================================================================ projects
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    workspace_path: str | None = Field(None, description="Absolute folder; created if missing. Default: data/workspaces/<name>")
    description: str = ""
    tech_stack: str = ""


def _slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower()[:60] or "project"


def validate_workspace(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        raise HTTPException(400, "workspace_path must be an absolute path")
    p = Path(os.path.realpath(p))
    if p.parent == p:
        raise HTTPException(400, "A drive root cannot be a workspace")
    for bad in FORBIDDEN_ROOTS:
        if p == bad:
            raise HTTPException(400, f"'{p}' is too broad to be a workspace; choose or create a project sub-folder")
    app_root = Path(__file__).resolve().parents[3]
    if p == app_root or app_root in p.parents and p == get_env().data_dir:
        raise HTTPException(400, "The application's own folder cannot be a workspace")
    return p


async def project_dict(p: Project, s: Session, with_git: bool = True) -> dict:
    root = (s.query(Task).filter(Task.project_id == p.id, Task.parent_id.is_(None)).order_by(Task.created_at.desc()).first())
    cur = root
    done = total = 0
    if root:
        kids = s.query(Task).filter(Task.parent_id == root.id).all()
        total = len(kids) or 1
        done = len([k for k in kids if k.status == TaskStatus.COMPLETED]) if kids else (1 if root.status == TaskStatus.COMPLETED else 0)
    g = await gitutil.status_summary(Path(p.workspace_path)) if with_git and Path(p.workspace_path).exists() else {}
    return {"id": p.id, "name": p.name, "workspace_path": p.workspace_path, "description": p.description,
            "tech_stack": p.tech_stack, "created_at": _iso(p.created_at), "last_activity": _iso(p.last_activity),
            "current_task": {"id": cur.id, "title": cur.title, "status": cur.status.value} if cur else None,
            "progress": {"done": done, "total": total}, "git": g, "run": orch.run_info(p.id),
            "workspace_exists": Path(p.workspace_path).exists()}


@router.get("/projects")
async def list_projects(s: Session = Depends(get_session)):
    rows = s.query(Project).order_by(Project.last_activity.desc()).all()
    return [await project_dict(p, s) for p in rows]


@router.post("/projects", status_code=201)
async def create_project(body: ProjectIn, s: Session = Depends(get_session)):
    ws = validate_workspace(body.workspace_path) if body.workspace_path else get_env().data_dir / "workspaces" / _slug(body.name)
    if s.query(Project).filter(Project.workspace_path == str(ws)).first():
        raise HTTPException(409, "A project already uses this workspace folder")
    ws.mkdir(parents=True, exist_ok=True)
    try:
        await gitutil.ensure_repo(ws)
    except Exception as e:
        raise HTTPException(500, f"Could not initialise git in workspace: {e}")
    p = Project(name=body.name, workspace_path=str(ws), description=body.description, tech_stack=body.tech_stack)
    s.add(p)
    s.flush()
    s.commit()  # commit before responding: the session dependency would otherwise commit AFTER the response is sent
    return await project_dict(p, s)


@router.get("/projects/{pid}")
async def get_project(pid: str, s: Session = Depends(get_session)):
    p = s.get(Project, pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return await project_dict(p, s)


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    tech_stack: str | None = None


@router.patch("/projects/{pid}")
async def patch_project(pid: str, body: ProjectPatch, s: Session = Depends(get_session)):
    p = s.get(Project, pid)
    if not p:
        raise HTTPException(404, "Project not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    s.commit()
    return await project_dict(p, s)


@router.delete("/projects/{pid}")
async def delete_project(pid: str, s: Session = Depends(get_session)):
    """Removes the project from ForgeAgent. Files in the workspace folder are NOT deleted."""
    p = s.get(Project, pid)
    if not p:
        raise HTTPException(404, "Project not found")
    if orch.is_running(pid):
        await orch.cancel(pid)
    s.query(Message).filter(Message.project_id == pid).delete()
    s.query(ToolRun).filter(ToolRun.project_id == pid).delete()
    s.query(Event).filter(Event.project_id == pid).delete()
    s.delete(p)
    s.commit()  # commit before responding: the session dependency would otherwise commit AFTER the response is sent
    return {"deleted": True, "files_kept_at": p.workspace_path}


def _project(s: Session, pid: str) -> Project:
    p = s.get(Project, pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


# ================================================================================ files
@router.get("/projects/{pid}/tree")
def tree(pid: str, path: str = ".", s: Session = Depends(get_session)):
    p = _project(s, pid)
    try:
        base = resolve_in_workspace(p.workspace_path, path, must_exist=True)
    except (WorkspaceViolation, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    if not base.is_dir():
        raise HTTPException(400, "Not a directory")
    root = Path(p.workspace_path).resolve()
    items = []
    for e in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if e.name in IGNORE_DIRS:
            continue
        items.append({"name": e.name, "path": e.relative_to(root).as_posix(), "is_dir": e.is_dir(),
                      "size": e.stat().st_size if e.is_file() else None})
    return items


@router.get("/projects/{pid}/file")
def read_file(pid: str, path: str, s: Session = Depends(get_session)):
    p = _project(s, pid)
    try:
        f = resolve_in_workspace(p.workspace_path, path, must_exist=True)
    except (WorkspaceViolation, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    if not f.is_file() or f.stat().st_size > 2_000_000:
        raise HTTPException(400, "Not a readable text file (missing or > 2MB)")
    raw = f.read_bytes()
    if b"\x00" in raw[:4096]:
        raise HTTPException(415, "Binary file")
    return {"path": path, "content": raw.decode("utf-8", "replace")}


class FileWrite(BaseModel):
    path: str
    content: str


@router.put("/projects/{pid}/file")
async def write_file(pid: str, body: FileWrite, s: Session = Depends(get_session)):
    p = _project(s, pid)
    try:
        f = resolve_in_workspace(p.workspace_path, body.path)
    except WorkspaceViolation as e:
        raise HTTPException(400, str(e))
    if touches_protected(p.workspace_path, f):
        raise HTTPException(400, "Protected path")
    async with orch.services.lock(pid, "fs"):
        atomic_write(f, body.content)
    bus.publish(pid, "files_changed", {"paths": [body.path], "by": "user"}, None, persist=False)
    return {"saved": True}


@router.get("/projects/{pid}/screenshots/{name}")
def screenshot(pid: str, name: str):
    f = get_env().screenshots_dir / pid / Path(name).name
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f)


# ================================================================================ chat / agent
class ChatIn(BaseModel):
    message: str = Field(min_length=1)
    mode: Literal["agent", "chat"] = "agent"
    plan: Literal["auto", "always", "never"] = "auto"


@router.get("/projects/{pid}/messages")
def messages(pid: str, limit: int = 200, s: Session = Depends(get_session)):
    _project(s, pid)
    rows = (s.query(Message).filter(Message.project_id == pid, Message.visible.is_(True)).order_by(Message.id.desc()).limit(limit).all())
    rows.reverse()
    return [{"id": m.id, "role": m.role, "agent_role": m.agent_role, "content": m.content, "task_id": m.task_id,
             "created_at": _iso(m.created_at)} for m in rows]


@router.post("/projects/{pid}/chat", status_code=202)
async def chat(pid: str, body: ChatIn, s: Session = Depends(get_session)):
    _project(s, pid)
    try:
        if body.mode == "chat":
            await orch.start_chat(pid, body.message)
            return {"started": "chat"}
        task_id = await orch.start_goal(pid, body.message, body.plan)
        return {"started": "agent", "task_id": task_id}
    except RunConflict as e:
        raise HTTPException(409, str(e))


@router.post("/projects/{pid}/stop")
async def stop(pid: str):
    return {"stopped": await orch.cancel(pid)}


@router.post("/projects/{pid}/pause")
def pause(pid: str):
    return {"paused": orch.pause(pid)}


@router.post("/projects/{pid}/unpause")
def unpause(pid: str):
    return {"resumed": orch.unpause(pid)}


@router.get("/projects/{pid}/run")
def run_info(pid: str):
    return orch.run_info(pid)


class ApprovalIn(BaseModel):
    approve: bool


@router.get("/approvals")
def approvals(project_id: str | None = None):
    return [a.public() for a in orch.approvals.values() if not project_id or a.project_id == project_id]


@router.post("/approvals/{aid}")
def resolve_approval(aid: str, body: ApprovalIn):
    if not orch.resolve_approval(aid, body.approve):
        raise HTTPException(404, "Approval not pending")
    return {"ok": True}


# ================================================================================ tasks
def _task_dict(t: Task) -> dict:
    return {"id": t.id, "project_id": t.project_id, "parent_id": t.parent_id, "title": t.title, "description": t.description,
            "role": t.role, "status": t.status.value, "priority": t.priority, "position": t.position, "depends_on": t.depends_on or [],
            "retries": t.retries, "steps_used": t.steps_used, "result": t.result, "error": t.error, "checkpoint_id": t.checkpoint_id,
            "created_at": _iso(t.created_at), "updated_at": _iso(t.updated_at)}


@router.get("/projects/{pid}/tasks")
def tasks(pid: str, s: Session = Depends(get_session)):
    _project(s, pid)
    rows = s.query(Task).filter(Task.project_id == pid).order_by(Task.created_at, Task.position).all()
    return [_task_dict(t) for t in rows]


class TaskIn(BaseModel):
    title: str
    description: str = ""
    priority: int = Field(5, ge=1, le=10)
    role: Literal["developer", "debugger", "tester", "reviewer", "architect"] = "developer"
    parent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)


@router.post("/projects/{pid}/tasks", status_code=201)
def create_task(pid: str, body: TaskIn, s: Session = Depends(get_session)):
    _project(s, pid)
    if body.parent_id:
        par = s.get(Task, body.parent_id)
        if not par or par.project_id != pid:
            raise HTTPException(400, "Unknown parent task")
    pos = (s.query(func.max(Task.position)).filter(Task.parent_id == body.parent_id, Task.project_id == pid).scalar() or 0) + 1
    t = Task(project_id=pid, title=body.title, description=body.description or body.title, priority=body.priority, role=body.role,
             parent_id=body.parent_id, depends_on=body.depends_on, position=pos, status=TaskStatus.PENDING)
    s.add(t)
    s.flush()
    s.commit()  # commit before responding: the session dependency would otherwise commit AFTER the response is sent
    return _task_dict(t)


@router.post("/tasks/{tid}/run", status_code=202)
async def run_task(tid: str, plan: Literal["auto", "always", "never"] = "auto"):
    try:
        return {"task_id": await orch.run_queued(tid, plan)}
    except KeyError as e:
        raise HTTPException(404, str(e))
    except RunConflict as e:
        raise HTTPException(409, str(e))


@router.post("/tasks/{tid}/resume", status_code=202)
async def resume_task(tid: str):
    try:
        return {"task_id": await orch.resume_task(tid)}
    except KeyError as e:
        raise HTTPException(404, str(e))
    except RunConflict as e:
        raise HTTPException(409, str(e))


@router.delete("/tasks/{tid}")
def delete_task(tid: str, s: Session = Depends(get_session)):
    t = s.get(Task, tid)
    if not t:
        raise HTTPException(404)
    if t.status in (TaskStatus.RUNNING, TaskStatus.PLANNING, TaskStatus.TESTING):
        raise HTTPException(409, "Task is running; stop it first")
    s.delete(t)
    s.commit()  # commit before responding: the session dependency would otherwise commit AFTER the response is sent
    return {"deleted": True}


# ================================================================================ audit / timeline
@router.get("/projects/{pid}/tool-runs")
def tool_runs(pid: str, limit: int = Query(100, le=500), tool: str | None = None, s: Session = Depends(get_session)):
    _project(s, pid)
    q = s.query(ToolRun).filter(ToolRun.project_id == pid)
    if tool:
        q = q.filter(ToolRun.tool == tool)
    rows = q.order_by(ToolRun.id.desc()).limit(limit).all()
    return [{"id": r.id, "task_id": r.task_id, "tool": r.tool, "arguments": r.arguments, "ok": r.ok, "status": r.status, "summary": r.summary,
             "files_changed": r.files_changed, "duration_ms": r.duration_ms, "approval": r.approval, "created_at": _iso(r.created_at)} for r in rows]


@router.get("/tool-runs/{rid}")
def tool_run(rid: int, s: Session = Depends(get_session)):
    r = s.get(ToolRun, rid)
    if not r:
        raise HTTPException(404)
    return {"id": r.id, "tool": r.tool, "arguments": r.arguments, "ok": r.ok, "status": r.status, "output": r.output, "summary": r.summary,
            "files_changed": r.files_changed, "duration_ms": r.duration_ms, "approval": r.approval, "created_at": _iso(r.created_at)}


@router.get("/projects/{pid}/test-results")
def test_results(pid: str, limit: int = 30, s: Session = Depends(get_session)):
    _project(s, pid)
    rows = (s.query(ToolRun).filter(ToolRun.project_id == pid, ToolRun.tool.in_(("run_tests", "run_build"))).order_by(ToolRun.id.desc()).limit(limit).all())
    return [{"id": r.id, "kind": "tests" if r.tool == "run_tests" else "build", "ok": r.ok, "summary": r.summary,
             "created_at": _iso(r.created_at), "duration_ms": r.duration_ms, "output_tail": r.output[-1500:]} for r in rows]


@router.get("/projects/{pid}/events")
def events(pid: str, after: int = 0, limit: int = Query(300, le=1000), s: Session = Depends(get_session)):
    _project(s, pid)
    rows = s.query(Event).filter(Event.project_id == pid, Event.id > after).order_by(Event.id.desc()).limit(limit).all()
    rows.reverse()
    return [{"id": e.id, "task_id": e.task_id, "type": e.type, "data": e.data, "ts": _iso(e.created_at)} for e in rows]


@router.websocket("/ws/projects/{pid}")
async def ws_project(ws: WebSocket, pid: str):
    token = get_env().api_token
    if token and ws.query_params.get("token") != token:
        await ws.close(code=4401)
        return
    await ws.accept()
    q = bus.subscribe(pid)
    await ws.send_json({"type": "hello", "run": orch.run_info(pid)})
    try:
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=20)
                await ws.send_json(ev)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(pid, q)


# ================================================================================ checkpoints
@router.get("/projects/{pid}/checkpoints")
def checkpoints(pid: str, s: Session = Depends(get_session)):
    _project(s, pid)
    rows = s.query(Checkpoint).filter(Checkpoint.project_id == pid).order_by(Checkpoint.created_at.desc()).limit(100).all()
    return [{"id": c.id, "commit": c.commit_hash, "label": c.label, "task_id": c.task_id, "created_at": _iso(c.created_at)} for c in rows]


class CheckpointIn(BaseModel):
    label: str = "manual checkpoint"


@router.post("/projects/{pid}/checkpoints", status_code=201)
async def make_checkpoint(pid: str, body: CheckpointIn, s: Session = Depends(get_session)):
    p = _project(s, pid)
    if orch.is_running(pid):
        raise HTTPException(409, "Stop the agent before creating a manual checkpoint")
    async with orch.services.lock(pid, "git"):
        h = await gitutil.checkpoint(Path(p.workspace_path), body.label)
    c = Checkpoint(project_id=pid, commit_hash=h, label=body.label)
    s.add(c)
    s.flush()
    s.commit()  # commit before responding: the session dependency would otherwise commit AFTER the response is sent
    return {"id": c.id, "commit": h, "label": c.label}


@router.post("/projects/{pid}/checkpoints/{cid}/restore")
async def restore_checkpoint(pid: str, cid: str, s: Session = Depends(get_session)):
    """User-initiated restore (the click is the approval)."""
    p = _project(s, pid)
    c = s.get(Checkpoint, cid)
    if not c or c.project_id != pid:
        raise HTTPException(404, "Checkpoint not found")
    if orch.is_running(pid):
        raise HTTPException(409, "Stop the agent before restoring a checkpoint")
    async with orch.services.lock(pid, "git"):
        try:
            await gitutil.restore(Path(p.workspace_path), c.commit_hash)
        except RuntimeError as e:
            raise HTTPException(500, str(e))
    bus.publish(pid, "files_changed", {"paths": ["*"], "by": "restore"}, None, persist=False)
    bus.publish(pid, "milestone", {"text": f"Workspace restored to checkpoint '{c.label}'"}, None)
    return {"restored": c.commit_hash}


# ================================================================================ memory
class MemoryIn(BaseModel):
    kind: str = "note"
    title: str = ""
    content: str
    pinned: bool = False


class MemoryPatch(BaseModel):
    kind: str | None = None
    title: str | None = None
    content: str | None = None
    pinned: bool | None = None


def _mem_dict(m: MemoryItem) -> dict:
    return {"id": m.id, "kind": m.kind, "title": m.title, "content": m.content, "pinned": m.pinned, "task_id": m.source_task_id,
            "updated_at": _iso(m.updated_at)}


@router.get("/projects/{pid}/memory")
def list_memory(pid: str, q: str | None = None, kind: str | None = None, s: Session = Depends(get_session)):
    _project(s, pid)
    if q:
        items = mem.search_memory(pid, q, 50)
        return [_mem_dict(m) for m in items if not kind or m.kind == kind]
    query = s.query(MemoryItem).filter(MemoryItem.project_id == pid)
    if kind:
        query = query.filter(MemoryItem.kind == kind)
    return [_mem_dict(m) for m in query.order_by(MemoryItem.updated_at.desc()).limit(500).all()]


@router.post("/projects/{pid}/memory", status_code=201)
def add_memory(pid: str, body: MemoryIn, s: Session = Depends(get_session)):
    _project(s, pid)
    mid = mem.add_memory(pid, body.kind, body.title or body.content[:60], body.content, pinned=body.pinned, dedupe=False)
    return {"id": mid}


@router.patch("/memory/{mid}")
def patch_memory(mid: int, body: MemoryPatch):
    if not mem.update_memory(mid, **body.model_dump()):
        raise HTTPException(404)
    return {"ok": True}


@router.delete("/memory/{mid}")
def delete_memory(mid: int):
    if not mem.delete_memory(mid):
        raise HTTPException(404)
    return {"ok": True}


# ================================================================================ settings
@router.get("/settings")
def get_settings():
    st = load_settings().model_dump()
    st["model"]["api_key"] = "***" if st["model"].get("api_key") else ""
    return st


@router.get("/settings/meta")
def settings_meta():
    return {"tools": sorted(REGISTRY), "roles": ROLE_TOOLS, "defaults": AppSettings().model_dump()}


@router.put("/settings")
def put_settings(body: AppSettings):
    cur = load_settings()
    if body.model.api_key == "***":
        body.model.api_key = cur.model.api_key
    save_settings(body)
    return get_settings()


# ================================================================================ model server
@router.get("/model/status")
async def model_status():
    st = load_settings()
    gw = LLMGateway(st.model, transport=orch.gateways.transport)
    try:
        info = await gw.health()
    finally:
        await gw.aclose()
    info["server_process"] = {"managed": llama.running, "cmdline": llama.cmdline if llama.running else None}
    info["configured_context"] = st.model.context_size
    return info


@router.post("/model/test")
async def model_test(prompt: str = "Reply with the single word: pong"):
    st = load_settings()
    gw = LLMGateway(st.model, transport=orch.gateways.transport)
    try:
        c = await gw.complete([{"role": "user", "content": prompt}], None, max_tokens=64)
        return {"ok": True, "reply": c.content, "tokens_per_second": round(c.tokens_per_second, 2), "prompt_tokens": c.prompt_tokens,
                "completion_tokens": c.completion_tokens, "duration_ms": c.duration_ms, "model": c.model}
    except LLMError as e:
        raise HTTPException(502, str(e))
    finally:
        await gw.aclose()


@router.get("/model/stats")
def model_stats(s: Session = Depends(get_session)):
    rows = s.query(LLMUsage).order_by(LLMUsage.id.desc()).limit(50).all()
    if not rows:
        return {"requests": 0}
    tps = [r.tokens_per_second for r in rows if r.tokens_per_second]
    return {"requests": s.query(func.count(LLMUsage.id)).scalar(), "avg_tokens_per_second": round(sum(tps) / len(tps), 2) if tps else None,
            "last_tokens_per_second": rows[0].tokens_per_second, "last_prompt_tokens": rows[0].prompt_tokens,
            "total_prompt_tokens": s.query(func.sum(LLMUsage.prompt_tokens)).scalar() or 0,
            "total_completion_tokens": s.query(func.sum(LLMUsage.completion_tokens)).scalar() or 0,
            "recent": [{"tps": r.tokens_per_second, "prompt": r.prompt_tokens, "completion": r.completion_tokens} for r in reversed(rows[:30])]}


@router.get("/model/files")
def model_files():
    return {"models_dir": get_env().models_dir, "llama_server": get_env().llama_server_path,
            "llama_server_exists": Path(get_env().llama_server_path).exists(), "files": llama.list_models()}


class ServerStart(BaseModel):
    model_path: str
    context_size: int = 16384
    gpu_layers: int | None = None
    kv_cache_type: Literal["f16", "q8_0", "q4_0"] = "q8_0"
    extra_args: str = ""


@router.post("/model/server/start")
async def server_start(body: ServerStart):
    try:
        info = await llama.start(body.model_path, body.context_size, body.gpu_layers, None, body.extra_args, body.kv_cache_type)
    except (FileNotFoundError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    st = load_settings()
    if st.model.context_size != body.context_size:
        st.model.context_size = body.context_size
        save_settings(st)
    return info


@router.post("/model/server/stop")
def server_stop():
    return {"stopped": llama.stop()}


@router.get("/model/server/log")
def server_log(lines: int = 80):
    return {"running": llama.running, "log": list(llama.log)[-lines:]}


# ================================================================================ system
def gpu_info() -> dict | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip().splitlines()[0]
        name, total, used, util, temp = [x.strip() for x in out.split(",")]
        return {"name": name, "vram_total_mb": int(total), "vram_used_mb": int(used), "util_pct": int(util), "temp_c": int(temp)}
    except Exception:
        return None


@router.get("/system/resources")
async def resources():
    vm = psutil.virtual_memory()
    gpu = await asyncio.to_thread(gpu_info)
    return {"cpu_pct": psutil.cpu_percent(interval=None), "ram_total_gb": round(vm.total / 2**30, 1), "ram_used_gb": round(vm.used / 2**30, 1),
            "gpu": gpu, "devservers": sum(1 for s in orch.services.devservers.servers.values() if s.running),
            "time": datetime.now(timezone.utc).isoformat()}


@router.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}
