"""Long-lived services shared by tools: per-project locks, dev servers, browser sessions."""
from __future__ import annotations

import asyncio
import socket
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import get_env
from .proc import kill_tree, spawn


@dataclass
class DevServer:
    name: str
    command: str
    cwd: Path
    port: int | None
    proc: asyncio.subprocess.Process
    log_path: Path
    started: float = field(default_factory=time.time)
    tail: deque = field(default_factory=lambda: deque(maxlen=400))
    pump: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.proc.returncode is None


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


class DevServerManager:
    def __init__(self) -> None:
        self.servers: dict[tuple[str, str], DevServer] = {}

    async def start(self, project_id: str, name: str, command: str, cwd: Path, port: int | None,
                    wait_s: int = 30) -> DevServer:
        key = (project_id, name)
        if key in self.servers and self.servers[key].running:
            raise RuntimeError(f"Dev server '{name}' is already running (pid {self.servers[key].proc.pid}). Stop it first.")
        if port and port_open(port):
            raise RuntimeError(f"Port {port} is already in use by another process.")
        log_path = get_env().logs_dir / f"{project_id}_{name}.log"
        proc = await spawn(command, cwd)
        srv = DevServer(name, command, cwd, port, proc, log_path)
        self.servers[key] = srv

        async def pump():
            assert proc.stdout is not None
            with log_path.open("w", encoding="utf-8", errors="replace") as f:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", "replace")
                    f.write(text)
                    f.flush()
                    srv.tail.append(text.rstrip("\n"))

        srv.pump = asyncio.create_task(pump())
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if not srv.running:
                break
            if port and port_open(port):
                break
            if not port and time.monotonic() - srv.started > 3:
                break
            await asyncio.sleep(0.4)
        return srv

    def stop(self, project_id: str, name: str) -> bool:
        srv = self.servers.get((project_id, name))
        if not srv:
            return False
        if srv.running:
            kill_tree(srv.proc.pid)
        return True

    def stop_all(self, project_id: str | None = None) -> int:
        n = 0
        for (pid, name), srv in list(self.servers.items()):
            if project_id is None or pid == project_id:
                if srv.running:
                    kill_tree(srv.proc.pid)
                    n += 1
        return n

    def list(self, project_id: str) -> list[DevServer]:
        return [s for (pid, _), s in self.servers.items() if pid == project_id]


# ---------------------------------------------------------------------------------------- browser
@dataclass
class BrowserSession:
    context: Any
    page: Any
    console: list[dict] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)


class BrowserManager:
    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    async def _ensure_browser(self):
        async with self._lock:
            if self._browser is None:
                try:
                    from playwright.async_api import async_playwright
                except ImportError as e:
                    raise RuntimeError("Playwright is not installed (pip install playwright)") from e
                self._pw = await async_playwright().start()
                try:
                    self._browser = await self._pw.chromium.launch(headless=True)
                except Exception as e:
                    await self._pw.stop()
                    self._pw = None
                    raise RuntimeError(
                        "Could not launch Chromium. Run: python -m playwright install chromium\n" + str(e)[:300]) from e
        return self._browser

    async def session(self, project_id: str) -> BrowserSession:
        if project_id in self._sessions:
            return self._sessions[project_id]
        browser = await self._ensure_browser()
        ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await ctx.new_page()
        sess = BrowserSession(ctx, page)

        def on_console(msg):
            if len(sess.console) < 500:
                sess.console.append({"type": msg.type, "text": msg.text[:500]})

        page.on("console", on_console)
        page.on("pageerror", lambda e: sess.page_errors.append(str(e)[:800]))
        page.on("requestfailed", lambda r: sess.failed_requests.append(f"{r.method} {r.url} - {r.failure}"))
        page.on("response", lambda r: sess.failed_requests.append(f"{r.request.method} {r.url} -> HTTP {r.status}") if r.status >= 400 else None)
        self._sessions[project_id] = sess
        return sess

    async def close_project(self, project_id: str) -> None:
        sess = self._sessions.pop(project_id, None)
        if sess:
            try:
                await sess.context.close()
            except Exception:
                pass

    async def shutdown(self) -> None:
        for pid in list(self._sessions):
            await self.close_project(pid)
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._browser = self._pw = None


class Services:
    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self.devservers = DevServerManager()
        self.browser = BrowserManager()
        self.full_outputs: dict[int, str] = {}

    def lock(self, project_id: str, name: str) -> asyncio.Lock:
        return self._locks[(project_id, name)]

    async def shutdown(self) -> None:
        self.devservers.stop_all()
        await self.browser.shutdown()
