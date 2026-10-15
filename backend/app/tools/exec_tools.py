"""Terminal, test, build, dev-server, log and database tools."""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from ..db import session_scope
from ..models import ToolRun
from ..security import redact, resolve_in_workspace, rel_display
from .base import Tool, ToolContext, ToolError, ToolResult, register
from .proc import find_shell, run_command, kill_tree
from .services import port_open


def _venv_python(root: Path) -> str:
    for cand in (root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python"):
        if cand.exists():
            return f'"{cand}"'
    return "python"


def _fmt_proc(r, header: str) -> str:
    status = "TIMED OUT" if r.timed_out else "CANCELLED" if r.cancelled else f"exit code {r.exit_code}"
    return f"{header}\n[{status}]\n{redact(r.output)}"


# ---------------------------------------------------------------------------------------- run_command
class RunCommandParams(BaseModel):
    command: str = Field(description="Shell command (bash syntax) executed in the workspace")
    cwd: str = Field(".", description="Working directory relative to the workspace root")
    timeout_s: int | None = Field(None, ge=1, le=3600)


@register
class RunCommand(Tool):
    name = "run_command"
    description = ("Run a terminal command in the workspace (installs, scripts, git, etc.). "
                   "Do NOT use for long-running servers - use start_dev_server. Shell: bash syntax.")
    Params = RunCommandParams
    category = "exec"
    mutates = True

    async def run(self, ctx, a: RunCommandParams) -> ToolResult:
        cwd = resolve_in_workspace(ctx.root, a.cwd, must_exist=True)
        timeout = a.timeout_s or ctx.settings.autonomy.command_timeout_s
        r = await run_command(a.command, cwd, timeout, ctx.cancel)
        ok = r.exit_code == 0 and not r.timed_out and not r.cancelled
        return ToolResult(ok, _fmt_proc(r, f"$ {a.command}"), {"exit_code": r.exit_code, "timed_out": r.timed_out},
                          summary=f"`{a.command[:80]}` -> {'ok' if ok else 'failed (' + str(r.exit_code) + ')'}", untrusted=True)


# ---------------------------------------------------------------------------------------- run_tests
class RunTestsParams(BaseModel):
    command: str | None = Field(None, description="Test command. Omit to auto-detect (pytest / npm test)")
    cwd: str = "."
    timeout_s: int | None = Field(None, ge=1, le=3600)


def detect_test_command(root: Path) -> str | None:
    pkg = root / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
            if "test" in scripts and "no test specified" not in scripts["test"]:
                return "npm test"
        except (OSError, json.JSONDecodeError):
            pass
    py_markers = list(root.glob("test_*.py")) + list(root.glob("tests/**/*.py")) + list(root.glob("*/tests/**/*.py")) + list(root.glob("**/test_*.py"))
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists() or py_markers:
        return f"{_venv_python(root)} -m pytest -q -x --no-header -p no:cacheprovider"
    return None


def parse_test_counts(output: str) -> dict:
    d: dict = {}
    for key, rx in (("passed", r"(\d+) passed"), ("failed", r"(\d+) failed"), ("errors", r"(\d+) errors?\b"), ("skipped", r"(\d+) skipped")):
        m = re.search(rx, output)
        if m:
            d[key] = int(m.group(1))
    m = re.search(r"Tests:\s+(?:(\d+) failed, )?(?:(\d+) passed)?", output)  # jest / vitest
    if m:
        if m.group(1):
            d["failed"] = int(m.group(1))
        if m.group(2):
            d["passed"] = int(m.group(2))
    return d


@register
class RunTests(Tool):
    name = "run_tests"
    description = "Run the project's test suite and return the real results. Auto-detects pytest or npm test."
    Params = RunTestsParams
    category = "exec"
    mutates = True

    def resolve_command(self, ctx: ToolContext, a: RunTestsParams) -> str:
        cwd = resolve_in_workspace(ctx.root, a.cwd, must_exist=True)
        cmd = a.command or detect_test_command(cwd)
        if not cmd:
            raise ToolError("Could not detect a test command (no package.json test script / pytest files). Pass `command`.")
        return cmd

    async def run(self, ctx, a: RunTestsParams) -> ToolResult:
        cwd = resolve_in_workspace(ctx.root, a.cwd, must_exist=True)
        cmd = self.resolve_command(ctx, a)
        r = await run_command(cmd, cwd, a.timeout_s or ctx.settings.autonomy.command_timeout_s, ctx.cancel)
        counts = parse_test_counts(r.output)
        ok = r.exit_code == 0 and not r.timed_out and not r.cancelled
        head = f"Test run: {'PASSED' if ok else 'FAILED'} {counts if counts else ''}\n$ {cmd}"
        return ToolResult(ok, _fmt_proc(r, head), {"exit_code": r.exit_code, "counts": counts, "command": cmd, "kind": "tests"},
                          summary=f"tests {'passed' if ok else 'FAILED'} {counts}", untrusted=True)


# ---------------------------------------------------------------------------------------- run_build
class RunBuildParams(BaseModel):
    command: str | None = Field(None, description="Build command. Omit to auto-detect (npm run build)")
    cwd: str = "."
    timeout_s: int | None = Field(None, ge=1, le=3600)


@register
class RunBuild(Tool):
    name = "run_build"
    description = "Run the project's build (e.g. `npm run build`, `tsc --noEmit`) and return the real output."
    Params = RunBuildParams
    category = "exec"
    mutates = True

    def resolve_command(self, ctx: ToolContext, a: RunBuildParams) -> str:
        cwd = resolve_in_workspace(ctx.root, a.cwd, must_exist=True)
        if a.command:
            return a.command
        pkg = cwd / "package.json"
        if pkg.exists():
            try:
                if "build" in json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}):
                    return "npm run build"
            except (OSError, json.JSONDecodeError):
                pass
        if (cwd / "pyproject.toml").exists() or any(cwd.glob("*.py")):
            return f"{_venv_python(cwd)} -m compileall -q ."
        raise ToolError("Could not detect a build command. Pass `command`.")

    async def run(self, ctx, a: RunBuildParams) -> ToolResult:
        cwd = resolve_in_workspace(ctx.root, a.cwd, must_exist=True)
        cmd = self.resolve_command(ctx, a)
        r = await run_command(cmd, cwd, a.timeout_s or max(ctx.settings.autonomy.command_timeout_s, 600), ctx.cancel)
        ok = r.exit_code == 0 and not r.timed_out and not r.cancelled
        return ToolResult(ok, _fmt_proc(r, f"Build: {'OK' if ok else 'FAILED'}\n$ {cmd}"),
                          {"exit_code": r.exit_code, "command": cmd, "kind": "build"}, summary=f"build {'ok' if ok else 'FAILED'}", untrusted=True)


# ---------------------------------------------------------------------------------------- dev servers
class StartServerParams(BaseModel):
    command: str = Field(description="Command that starts the server, e.g. 'npm run dev' or 'python -m uvicorn app.main:app --port 8001'")
    name: str = Field("dev", description="Name to refer to this server")
    port: int | None = Field(None, description="Port the server listens on (used to wait until it is ready)")
    cwd: str = "."


@register
class StartDevServer(Tool):
    name = "start_dev_server"
    description = "Start a long-running development server in the background and wait until its port is ready."
    Params = StartServerParams
    category = "exec"
    mutates = True

    async def run(self, ctx, a: StartServerParams) -> ToolResult:
        cwd = resolve_in_workspace(ctx.root, a.cwd, must_exist=True)
        try:
            srv = await ctx.services.devservers.start(ctx.project_id, a.name, a.command, cwd, a.port)
        except RuntimeError as e:
            raise ToolError(str(e))
        await asyncio.sleep(0.3)
        tail = "\n".join(list(srv.tail)[-25:])
        if not srv.running:
            return ToolResult(False, f"Server exited immediately (code {srv.proc.returncode}).\n{redact(tail)}", summary="dev server failed to start", untrusted=True)
        ready = (a.port is None) or port_open(a.port)
        msg = f"Server '{a.name}' started (pid {srv.proc.pid})" + (f", port {a.port} {'is accepting connections' if ready else 'NOT ready yet'}." if a.port else ".")
        return ToolResult(ready, f"{msg}\n--- recent log ---\n{redact(tail)}", {"pid": srv.proc.pid, "ready": ready},
                          summary=msg, untrusted=True)


class StopServerParams(BaseModel):
    name: str = "dev"


@register
class StopDevServer(Tool):
    name = "stop_dev_server"
    description = "Stop a development server started with start_dev_server."
    Params = StopServerParams
    category = "exec"
    mutates = True

    async def run(self, ctx, a: StopServerParams) -> ToolResult:
        ok = ctx.services.devservers.stop(ctx.project_id, a.name)
        if not ok:
            raise ToolError(f"No dev server named '{a.name}'.")
        return ToolResult(True, f"Stopped dev server '{a.name}'.", summary=f"stopped {a.name}")


# ---------------------------------------------------------------------------------------- inspect_logs
class InspectLogsParams(BaseModel):
    source: str = Field("dev_server", description="'dev_server' (live server output), 'tool_runs' (recent tool failures), or 'file' (a log file in the workspace)")
    name: str = Field("dev", description="Dev server name when source=dev_server")
    path: str | None = Field(None, description="Log file path when source=file")
    lines: int = Field(80, ge=1, le=500)
    grep: str | None = Field(None, description="Only lines containing this text (case-insensitive)")


@register
class InspectLogs(Tool):
    name = "inspect_logs"
    description = "Read application logs: dev server output, recent failed tool runs, or a log file in the workspace."
    Params = InspectLogsParams
    category = "read"

    async def run(self, ctx, a: InspectLogsParams) -> ToolResult:
        lines: list[str]
        if a.source == "dev_server":
            srv = ctx.services.devservers.servers.get((ctx.project_id, a.name))
            if not srv:
                raise ToolError(f"No dev server named '{a.name}' has been started.")
            lines = list(srv.tail) if srv.running else (srv.log_path.read_text(encoding="utf-8", errors="replace").splitlines() if srv.log_path.exists() else [])
            lines = [f"[server {'running' if srv.running else 'EXITED code ' + str(srv.proc.returncode)}]"] + lines
        elif a.source == "tool_runs":
            with session_scope() as s:
                rows = (s.query(ToolRun).filter(ToolRun.project_id == ctx.project_id, ToolRun.ok.is_(False))
                        .order_by(ToolRun.id.desc()).limit(10).all())
                lines = [f"#{r.id} {r.tool} [{r.status}] {r.summary}" for r in rows]
        elif a.source == "file":
            if not a.path:
                raise ToolError("path is required for source=file")
            p = resolve_in_workspace(ctx.root, a.path, must_exist=True)
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        else:
            raise ToolError("source must be dev_server, tool_runs or file")
        if a.grep:
            lines = [ln for ln in lines if a.grep.lower() in ln.lower()]
        out = redact("\n".join(lines[-a.lines:]))
        return ToolResult(True, out or "(no log lines)", {"lines": len(lines)}, summary=f"{len(lines)} log lines", untrusted=True)


# ---------------------------------------------------------------------------------------- query_database
class QueryDbParams(BaseModel):
    database: str = Field(description="Path to a SQLite database file inside the workspace")
    sql: str = Field(description="A single read-only SELECT / PRAGMA / EXPLAIN statement")
    max_rows: int = Field(50, ge=1, le=500)


@register
class QueryDatabase(Tool):
    name = "query_database"
    description = "Run a read-only SQL query against a SQLite database file in the workspace."
    Params = QueryDbParams
    category = "read"

    async def run(self, ctx, a: QueryDbParams) -> ToolResult:
        p = resolve_in_workspace(ctx.root, a.database, must_exist=True)
        sql = a.sql.strip().rstrip(";")
        if ";" in sql or not re.match(r"(?is)^\s*(select|with|pragma|explain)\b", sql):
            raise ToolError("Only a single read-only SELECT/WITH/PRAGMA/EXPLAIN statement is allowed.")

        def work():
            con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5)
            try:
                con.execute("PRAGMA query_only=ON")
                cur = con.execute(sql)
                cols = [d[0] for d in cur.description or []]
                rows = cur.fetchmany(a.max_rows)
                return cols, rows
            finally:
                con.close()

        try:
            cols, rows = await asyncio.to_thread(work)
        except sqlite3.Error as e:
            raise ToolError(f"SQL error: {e}")
        table = [" | ".join(cols)] + [" | ".join(str(v)[:80] for v in r) for r in rows]
        return ToolResult(True, "\n".join(table), {"rows": len(rows)}, summary=f"{len(rows)} rows", untrusted=True)


# ---------------------------------------------------------------------------------------- read_full_output
class ReadFullOutputParams(BaseModel):
    tool_run_id: int
    offset: int = Field(0, ge=0, description="Character offset to start from")
    length: int = Field(6000, ge=100, le=20000)


@register
class ReadFullOutput(Tool):
    name = "read_full_output"
    description = "Read more of a previous tool result that was truncated in the conversation (use the tool_run_id shown)."
    Params = ReadFullOutputParams
    category = "read"

    async def run(self, ctx, a: ReadFullOutputParams) -> ToolResult:
        with session_scope() as s:
            row = s.get(ToolRun, a.tool_run_id)
            if not row or row.project_id != ctx.project_id:
                raise ToolError("Unknown tool_run_id")
            text = row.output
        chunk = text[a.offset:a.offset + a.length]
        more = a.offset + a.length < len(text)
        return ToolResult(True, chunk + (f"\n[more available: next offset {a.offset + a.length} of {len(text)}]" if more else ""),
                          summary=f"output #{a.tool_run_id} @{a.offset}", untrusted=True)


__all__ = ["detect_test_command", "parse_test_counts", "find_shell", "kill_tree", "sys", "rel_display"]
