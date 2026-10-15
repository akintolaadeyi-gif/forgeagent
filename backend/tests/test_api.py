"""API tests through FastAPI's TestClient (includes lifespan: Alembic migration + crash recovery)."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.app_settings import AppSettings, save_settings
from app.state import orchestrator


@pytest.fixture
def client(llm, tmp_path):
    from app.main import app

    orchestrator.gateways.transport = llm.transport
    orchestrator.gateways._gw = None  # a cached gateway would still point at the previous test's fake server
    s = AppSettings()
    s.model.base_url = "http://fake-llm/v1"
    save_settings(s)
    with TestClient(app) as c:
        c.tmp = tmp_path
        yield c
    orchestrator.gateways.transport = None


def mk(client, name="demo", path=None):
    ws = path or str(client.tmp / name)
    r = client.post("/api/projects", json={"name": name, "workspace_path": ws})
    assert r.status_code == 201, r.text
    return r.json()


def wait_idle(client, pid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not client.get(f"/api/projects/{pid}/run").json()["active"]:
            return
        time.sleep(0.1)
    raise TimeoutError


def test_migrations_applied_and_health(client):
    assert client.get("/api/health").json()["status"] == "ok"
    from app.db import get_engine
    from sqlalchemy import inspect

    names = set(inspect(get_engine()).get_table_names())
    assert {"projects", "tasks", "messages", "tool_runs", "memory_items", "checkpoints", "events", "settings", "llm_usage", "alembic_version"} <= names


def test_project_lifecycle_and_workspace_validation(client, tmp_path):
    p = mk(client, "alpha")
    assert p["git"]["is_repo"] and os.path.isdir(p["workspace_path"])
    assert client.get(f"/api/projects/{p['id']}").json()["name"] == "alpha"
    assert any(x["id"] == p["id"] for x in client.get("/api/projects").json())
    assert client.post("/api/projects", json={"name": "dup", "workspace_path": p["workspace_path"]}).status_code == 409
    for bad in ("relative/path", "C:\\", os.path.expanduser("~"), os.environ.get("SystemRoot", "C:\\Windows")):
        assert client.post("/api/projects", json={"name": "bad", "workspace_path": bad}).status_code == 400, bad
    d = client.delete(f"/api/projects/{p['id']}").json()
    assert d["deleted"] and os.path.isdir(p["workspace_path"])  # files are kept
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


def test_files_api_and_traversal(client):
    p = mk(client)
    pid = p["id"]
    assert client.put(f"/api/projects/{pid}/file", json={"path": "src/app.py", "content": "print(1)\n"}).json()["saved"]
    assert client.get(f"/api/projects/{pid}/file", params={"path": "src/app.py"}).json()["content"] == "print(1)\n"
    tree = client.get(f"/api/projects/{pid}/tree").json()
    assert any(e["name"] == "src" and e["is_dir"] for e in tree)
    for bad in ("../x.txt", "..\\..\\x.txt", "C:/Windows/win.ini"):
        assert client.get(f"/api/projects/{pid}/file", params={"path": bad}).status_code == 400
        assert client.put(f"/api/projects/{pid}/file", json={"path": bad, "content": "x"}).status_code == 400
    assert client.put(f"/api/projects/{pid}/file", json={"path": ".git/config", "content": "x"}).status_code == 400


def test_settings_roundtrip_and_validation(client):
    s = client.get("/api/settings").json()
    assert s["autonomy"]["mode"] == "project_autonomous" and s["model"]["api_key"] == "***"
    s["autonomy"]["mode"] = "custom"
    s["autonomy"]["custom_permissions"] = {"delete_file": "deny"}
    s["prompts"]["identity"] = "You are Test."
    assert client.put("/api/settings", json=s).status_code == 200
    again = client.get("/api/settings").json()
    assert again["autonomy"]["custom_permissions"] == {"delete_file": "deny"} and again["prompts"]["identity"] == "You are Test."
    from app.app_settings import load_settings

    assert load_settings().model.api_key != "***"  # masked value never overwrites the stored key
    bad = client.get("/api/settings").json()
    bad["model"]["context_size"] = 100
    assert client.put("/api/settings", json=bad).status_code == 422
    bad = client.get("/api/settings").json()
    bad["autonomy"]["mode"] = "yolo"
    assert client.put("/api/settings", json=bad).status_code == 422


def test_memory_crud_and_search(client):
    pid = mk(client)["id"]
    a = client.post(f"/api/projects/{pid}/memory", json={"kind": "decision", "title": "Database", "content": "We use PostgreSQL with SQLAlchemy for persistence", "pinned": True}).json()["id"]
    client.post(f"/api/projects/{pid}/memory", json={"kind": "convention", "title": "Style", "content": "Frontend components are written in TypeScript with Tailwind"})
    hits = client.get(f"/api/projects/{pid}/memory", params={"q": "which database for persistence"}).json()
    assert hits and hits[0]["id"] == a
    assert client.patch(f"/api/memory/{a}", json={"content": "We use SQLite"}).status_code == 200
    assert any("SQLite" in m["content"] for m in client.get(f"/api/projects/{pid}/memory").json())
    assert client.delete(f"/api/memory/{a}").status_code == 200
    assert len(client.get(f"/api/projects/{pid}/memory").json()) == 1


def test_chat_mode_streams_over_websocket(client, llm):
    pid = mk(client)["id"]
    llm.reply("Hello from the local model!")
    with client.websocket_connect(f"/api/ws/projects/{pid}") as ws:
        assert ws.receive_json()["type"] == "hello"
        assert client.post(f"/api/projects/{pid}/chat", json={"message": "hi", "mode": "chat"}).status_code == 202
        seen, text = [], ""
        for _ in range(60):
            ev = ws.receive_json()
            seen.append(ev["type"])
            if ev["type"] == "assistant_delta":
                text += ev["data"]["text"]
            if ev["type"] == "run_finished":
                break
    assert "assistant_message" in seen and text == "Hello from the local model!"
    msgs = client.get(f"/api/projects/{pid}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    stats = client.get("/api/model/stats").json()
    assert stats["requests"] >= 1


def test_agent_run_live_events_and_audit(client, llm):
    pid = mk(client)["id"]
    llm.reply("", [("create_file", {"path": "hello.txt", "content": "hi"})])
    llm.reply("", [("complete_task", {"status": "success", "summary": "done"})])
    with client.websocket_connect(f"/api/ws/projects/{pid}") as ws:
        ws.receive_json()
        r = client.post(f"/api/projects/{pid}/chat", json={"message": "create hello.txt", "plan": "never"})
        assert r.status_code == 202
        types = []
        for _ in range(200):
            ev = ws.receive_json()
            types.append(ev["type"])
            if ev["type"] == "run_finished":
                break
    for needed in ("task_status", "tool_start", "tool_end", "files_changed", "checkpoint", "usage"):
        assert needed in types, (needed, types)
    runs = client.get(f"/api/projects/{pid}/tool-runs").json()
    assert [r["tool"] for r in reversed(runs)] == ["create_file", "complete_task"]
    detail = client.get(f"/api/tool-runs/{runs[-1]['id']}").json()
    assert "Created hello.txt" in detail["output"] and detail["files_changed"] == ["hello.txt"]
    tasks = client.get(f"/api/projects/{pid}/tasks").json()
    assert tasks[0]["status"] == "COMPLETED"
    assert client.get(f"/api/projects/{pid}/events").json()  # persisted timeline


def test_busy_project_rejects_second_run_and_stop(client, llm):
    s = client.get("/api/settings").json()
    s["autonomy"]["mode"] = "supervised"
    client.put("/api/settings", json=s)
    pid = mk(client)["id"]
    llm.reply("", [("create_file", {"path": "a.txt", "content": "x"})])
    assert client.post(f"/api/projects/{pid}/chat", json={"message": "go", "plan": "never"}).status_code == 202
    for _ in range(100):
        if client.get("/api/approvals", params={"project_id": pid}).json():
            break
        time.sleep(0.05)
    assert client.post(f"/api/projects/{pid}/chat", json={"message": "again", "plan": "never"}).status_code == 409
    assert client.post(f"/api/projects/{pid}/pause").json()["paused"]
    assert client.post(f"/api/projects/{pid}/unpause").json()["resumed"]
    assert client.post(f"/api/projects/{pid}/stop").json()["stopped"]
    assert not client.get(f"/api/projects/{pid}/run").json()["active"]
    assert client.get(f"/api/projects/{pid}/tasks").json()[0]["status"] == "CANCELLED"


def test_task_queue_run_and_resume_endpoints(client, llm):
    pid = mk(client)["id"]
    t = client.post(f"/api/projects/{pid}/tasks", json={"title": "Queued job", "description": "create q.txt", "priority": 3}).json()
    assert t["status"] == "PENDING" and t["priority"] == 3
    llm.reply("", [("create_file", {"path": "q.txt", "content": "q"})])
    llm.reply("", [("complete_task", {"status": "success", "summary": "ok"})])
    assert client.post(f"/api/tasks/{t['id']}/run", params={"plan": "never"}).status_code == 202
    wait_idle(client, pid)
    assert client.get(f"/api/projects/{pid}/tasks").json()[0]["status"] == "COMPLETED"
    assert client.post(f"/api/tasks/{t['id']}/resume").status_code == 409  # already complete
    assert client.post("/api/tasks/nope/resume").status_code == 404


def test_checkpoint_api_restore(client, llm):
    p = mk(client)
    pid, ws = p["id"], p["workspace_path"]
    client.put(f"/api/projects/{pid}/file", json={"path": "f.txt", "content": "v1"})
    c1 = client.post(f"/api/projects/{pid}/checkpoints", json={"label": "v1"}).json()
    client.put(f"/api/projects/{pid}/file", json={"path": "f.txt", "content": "v2"})
    client.put(f"/api/projects/{pid}/file", json={"path": "junk.txt", "content": "j"})
    assert client.post(f"/api/projects/{pid}/checkpoints/{c1['id']}/restore").status_code == 200
    assert open(os.path.join(ws, "f.txt")).read() == "v1" and not os.path.exists(os.path.join(ws, "junk.txt"))
    assert len(client.get(f"/api/projects/{pid}/checkpoints").json()) == 1


def test_model_endpoints(client, llm):
    st = client.get("/api/model/status").json()
    assert st["connected"] and st["model"] == "fake-model"
    llm.reply("pong")
    r = client.post("/api/model/test").json()
    assert r["ok"] and r["reply"] == "pong"


def test_timestamps_carry_a_utc_offset(client):
    """Regression: naive timestamps were read as local time by browsers (everything looked 1 h old in UTC+1)."""
    p = mk(client)
    for ts in (p["created_at"], p["last_activity"]):
        assert ts.endswith("+00:00") or ts.endswith("Z"), ts
    client.post(f"/api/projects/{p['id']}/memory", json={"content": "x"})
    assert client.get(f"/api/projects/{p['id']}/memory").json()[0]["updated_at"].endswith("+00:00")


def test_resources_endpoint(client):
    r = client.get("/api/system/resources").json()
    assert r["ram_total_gb"] > 0 and "cpu_pct" in r
