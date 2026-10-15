"""Subprocess execution with timeouts, cancellation and process-tree kill (Windows & POSIX)."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_CAPTURE = 400_000  # chars kept per command


@dataclass
class ProcResult:
    exit_code: int | None
    output: str
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False


def find_shell() -> tuple[list[str], str]:
    """Return (argv prefix, human-readable name). Prefers Git Bash (bash syntax is the best-known by models)."""
    if os.name != "nt":
        return ["/bin/bash", "-c"], "bash"
    candidates = [
        os.environ.get("FORGE_SHELL", ""),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    git = shutil.which("git")
    if git:
        candidates.append(str(Path(git).parents[1] / "bin" / "bash.exe"))
    for c in candidates:
        if c and Path(c).exists():
            return [c, "-c"], "bash (Git for Windows)"
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"], "PowerShell"


def kill_tree(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
        else:
            import signal

            pgid = os.getpgid(pid)
            if pgid == os.getpgrp():  # never signal our own process group
                os.kill(pid, signal.SIGKILL)
            else:
                os.killpg(pgid, signal.SIGKILL)
    except Exception:
        pass


def base_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "CI": "1", "FORCE_COLOR": "0", "NO_COLOR": "1"})
    # never leak the agent platform's own configuration into project processes
    for k in list(env):
        if k.startswith("FORGE_"):
            env.pop(k)
    if extra:
        env.update(extra)
    return env


async def spawn(cmd: str, cwd: Path, env: dict[str, str] | None = None, stdout=asyncio.subprocess.PIPE):
    argv, _ = find_shell()
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    return await asyncio.create_subprocess_exec(
        *argv, cmd, cwd=str(cwd), env=base_env(env), stdin=asyncio.subprocess.DEVNULL,
        stdout=stdout, stderr=asyncio.subprocess.STDOUT, **kwargs)


async def run_command(cmd: str, cwd: Path, timeout_s: int, cancel: asyncio.Event | None = None,
                      env: dict[str, str] | None = None) -> ProcResult:
    proc = await spawn(cmd, cwd, env)
    chunks: list[bytes] = []
    size = 0
    truncated = False

    async def reader():
        nonlocal size, truncated
        assert proc.stdout is not None
        while True:
            data = await proc.stdout.read(8192)
            if not data:
                break
            if size < MAX_CAPTURE * 2:
                chunks.append(data)
                size += len(data)
            else:
                truncated = True

    read_task = asyncio.create_task(reader())
    wait_task = asyncio.create_task(proc.wait())
    cancel_task = asyncio.create_task(cancel.wait()) if cancel else None
    waiters = {wait_task} | ({cancel_task} if cancel_task else set())
    timed_out = cancelled = False
    try:
        done, _ = await asyncio.wait(waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
        if wait_task not in done:
            if cancel_task and cancel_task in done:
                cancelled = True
            else:
                timed_out = True
            kill_tree(proc.pid)
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                pass
        try:
            await asyncio.wait_for(read_task, 5)
        except asyncio.TimeoutError:
            read_task.cancel()
    finally:
        for t in (wait_task, cancel_task):
            if t and not t.done():
                t.cancel()
    text = b"".join(chunks).decode("utf-8", "replace")
    if len(text) > MAX_CAPTURE:
        text = text[:MAX_CAPTURE // 2] + "\n...[output truncated]...\n" + text[-MAX_CAPTURE // 2:]
        truncated = True
    return ProcResult(exit_code=proc.returncode, output=text, timed_out=timed_out, cancelled=cancelled, truncated=truncated)


async def run_argv(argv: list[str], cwd: Path, timeout_s: int = 60, stdin: str | None = None) -> ProcResult:
    """Run a fixed argv (no shell) - used for git etc."""
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd), env=base_env(), stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **kwargs)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(stdin.encode() if stdin is not None else None), timeout_s)
    except asyncio.TimeoutError:
        kill_tree(proc.pid)
        return ProcResult(exit_code=None, output="timed out", timed_out=True)
    return ProcResult(exit_code=proc.returncode, output=out.decode("utf-8", "replace"))


if __name__ == "__main__":  # quick manual check
    print(find_shell(), sys.version)
