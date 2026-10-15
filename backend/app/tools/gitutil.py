"""Git helpers (checkpoints, status, restore)."""
from __future__ import annotations

from pathlib import Path

from .proc import ProcResult, run_argv

IDENT = ["-c", "user.name=ForgeAgent", "-c", "user.email=forgeagent@localhost", "-c", "core.autocrlf=false",
         "-c", "commit.gpgsign=false"]

DEFAULT_GITIGNORE = """node_modules/
.venv/
venv/
__pycache__/
*.pyc
.pytest_cache/
dist/
build/
.env
.env.*
!.env.example
.forge/
*.log
.DS_Store
"""


async def git(workspace: Path, *args: str, timeout_s: int = 60, stdin: str | None = None) -> ProcResult:
    return await run_argv(["git", *IDENT, *args], workspace, timeout_s, stdin)


async def is_repo(workspace: Path) -> bool:
    r = await git(workspace, "rev-parse", "--is-inside-work-tree")
    return r.exit_code == 0 and "true" in r.output


async def ensure_repo(workspace: Path) -> bool:
    """Initialise a repo in the workspace if it is not one. Returns True when a new repo was created."""
    workspace.mkdir(parents=True, exist_ok=True)
    top = await git(workspace, "rev-parse", "--show-toplevel")
    if top.exit_code == 0 and Path(top.output.strip()).resolve() == workspace.resolve():
        return False
    r = await git(workspace, "init")
    if r.exit_code != 0:
        raise RuntimeError(f"git init failed: {r.output}")
    gi = workspace / ".gitignore"
    if not gi.exists():
        gi.write_text(DEFAULT_GITIGNORE, encoding="utf-8")
    await git(workspace, "add", "-A")
    await git(workspace, "commit", "--allow-empty", "-m", "forge: initial state")
    return True


async def head(workspace: Path) -> str | None:
    r = await git(workspace, "rev-parse", "HEAD")
    return r.output.strip() if r.exit_code == 0 else None


async def checkpoint(workspace: Path, label: str) -> str:
    await git(workspace, "add", "-A")
    r = await git(workspace, "commit", "--allow-empty", "-m", f"forge checkpoint: {label}"[:200])
    if r.exit_code != 0:
        raise RuntimeError(f"git commit failed: {r.output}")
    h = await head(workspace)
    if not h:
        raise RuntimeError("could not read HEAD after commit")
    return h


async def restore(workspace: Path, commit: str) -> str:
    chk = await git(workspace, "cat-file", "-t", commit)
    if chk.exit_code != 0 or "commit" not in chk.output:
        raise RuntimeError(f"unknown commit {commit}")
    r = await git(workspace, "reset", "--hard", commit)
    if r.exit_code != 0:
        raise RuntimeError(r.output)
    await git(workspace, "clean", "-fd")  # removes untracked files created after the checkpoint (ignored files kept)
    return r.output.strip()


async def status_summary(workspace: Path) -> dict:
    """Short info for the dashboard."""
    if not (workspace / ".git").exists():
        return {"is_repo": False, "branch": None, "dirty": 0, "last_commit": None}
    b = await git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    s = await git(workspace, "status", "--porcelain")
    lc = await git(workspace, "log", "-1", "--pretty=%h %s")
    return {"is_repo": True, "branch": b.output.strip() if b.exit_code == 0 else None,
            "dirty": len([ln for ln in s.output.splitlines() if ln.strip()]) if s.exit_code == 0 else 0,
            "last_commit": lc.output.strip() if lc.exit_code == 0 else None}
