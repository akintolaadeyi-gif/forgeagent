from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

_TMP = tempfile.mkdtemp(prefix="forge-test-")
# agent-spawned `python -m pytest` must resolve to the interpreter that has pytest installed
os.environ["PATH"] = str(Path(__import__("sys").executable).parent) + os.pathsep + os.environ.get("PATH", "")
os.environ["FORGE_DATA_DIR"] = _TMP
os.environ["FORGE_LLM_BASE_URL"] = "http://fake-llm/v1"

from app import db as dbmod  # noqa: E402
from app.config import get_env  # noqa: E402

get_env.cache_clear()


class ScriptedLLM:
    """Fake llama.cpp: pops one scripted reply per /chat/completions call and streams it as SSE."""

    def __init__(self) -> None:
        self.script: list[dict] = []
        self.requests: list[dict] = []
        self.fail_with: list[int] = []  # HTTP statuses to return before succeeding
        self.reject_tools_once = False
        self.die_when_empty = False  # simulate the model server crashing once the script runs out

    def reply(self, content: str = "", tool_calls: list[tuple[str, dict]] | None = None) -> "ScriptedLLM":
        self.script.append({"content": content, "tool_calls": tool_calls or []})
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "fake-model", "meta": {"n_ctx_train": 32768}}]})
        if path.endswith("/props"):
            return httpx.Response(200, json={"default_generation_settings": {"n_ctx": 16384}, "chat_template": "{{ tools }}"})
        if path.endswith("/tokenize"):
            return httpx.Response(200, json={"tokens": list(range(10))})
        body = json.loads(request.content)
        self.requests.append(body)
        if self.reject_tools_once and body.get("tools"):
            self.reject_tools_once = False
            return httpx.Response(400, json={"error": "tools not supported"})
        if self.fail_with:
            return httpx.Response(self.fail_with.pop(0), text="boom")
        if self.die_when_empty and not self.script:
            return httpx.Response(500, text="server died")
        spec = self.script.pop(0) if self.script else {"content": "(script exhausted)", "tool_calls": []}
        chunks = []
        if spec["content"]:
            for i in range(0, len(spec["content"]), 7):
                chunks.append({"choices": [{"delta": {"content": spec["content"][i:i + 7]}}]})
        for idx, (name, args) in enumerate(spec["tool_calls"]):
            raw = args if isinstance(args, str) else json.dumps(args)
            chunks.append({"choices": [{"delta": {"tool_calls": [{"index": idx, "id": f"call_{idx}_{len(self.requests)}", "type": "function",
                                                                  "function": {"name": name, "arguments": raw[:5]}}]}}]})
            chunks.append({"choices": [{"delta": {"tool_calls": [{"index": idx, "function": {"arguments": raw[5:]}}]}}]})
        chunks.append({"choices": [{"delta": {}, "finish_reason": "tool_calls" if spec["tool_calls"] else "stop"}]})
        chunks.append({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}, "timings": {"predicted_per_second": 42.0}})
        sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture(scope="session", autouse=True)
def _db():
    dbmod.init_engine(f"sqlite:///{Path(_TMP, 'test.db').as_posix()}")
    dbmod.create_all()
    yield


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def workspace(tmp_path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest_asyncio.fixture
async def orch(llm):
    from app.agent.orchestrator import Orchestrator

    o = Orchestrator()
    o.gateways.transport = llm.transport
    yield o
    await o.services.shutdown()


@pytest_asyncio.fixture
async def project(workspace):
    from app.db import session_scope
    from app.models import Project
    from app.tools import gitutil

    await gitutil.ensure_repo(workspace)
    with session_scope() as s:
        p = Project(name="t", workspace_path=str(workspace))
        s.add(p)
        s.flush()
        pid = p.id
    return pid


async def wait_done(o, pid: str, timeout: float = 60):
    import asyncio

    h = o.runs[pid]
    await asyncio.wait_for(asyncio.shield(h.task), timeout)
