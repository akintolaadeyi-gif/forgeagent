"""Git inspection and checkpoint tools."""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..db import session_scope
from ..models import Checkpoint
from . import gitutil
from .base import Tool, ToolError, ToolResult, register


class GitChangesParams(BaseModel):
    diff: bool = Field(True, description="Include the diff (truncated) in addition to the status")
    path: str | None = Field(None, description="Limit the diff to this path")


@register
class GitChanges(Tool):
    name = "git_changes"
    description = "Show uncommitted changes (git status + diff) in the workspace."
    Params = GitChangesParams
    category = "git"

    async def run(self, ctx, a: GitChangesParams) -> ToolResult:
        if not (ctx.root / ".git").exists():
            raise ToolError("Workspace is not a git repository.")
        st = await gitutil.git(ctx.root, "status", "--short", "--branch")
        out = st.output
        if a.diff:
            args = ["diff", "HEAD", "--stat", "--patch", "--no-color"]
            if a.path:
                args += ["--", a.path]
            d = await gitutil.git(ctx.root, *args)
            out += "\n" + d.output[:12000] + ("\n...[diff truncated]" if len(d.output) > 12000 else "")
        return ToolResult(True, out.strip() or "(clean)", summary="git status/diff", untrusted=True)


class CheckpointParams(BaseModel):
    label: str = Field(description="Short description of the state being saved")


@register
class CreateCheckpoint(Tool):
    name = "create_checkpoint"
    description = "Commit the current workspace state as a restorable Git checkpoint."
    Params = CheckpointParams
    category = "git"
    mutates = True

    async def run(self, ctx, a: CheckpointParams) -> ToolResult:
        async with ctx.services.lock(ctx.project_id, "git"):
            await gitutil.ensure_repo(ctx.root)
            h = await gitutil.checkpoint(ctx.root, a.label)
        with session_scope() as s:
            cp = Checkpoint(project_id=ctx.project_id, task_id=ctx.task_id, commit_hash=h, label=a.label)
            s.add(cp)
            s.flush()
            cid = cp.id
        return ToolResult(True, f"Checkpoint {cid} created at commit {h[:10]}: {a.label}", {"checkpoint_id": cid, "commit": h},
                          summary=f"checkpoint {cid}")


class RestoreParams(BaseModel):
    checkpoint_id: str = Field(description="Checkpoint id (from create_checkpoint / list) or a commit hash")


@register
class RestoreCheckpoint(Tool):
    name = "restore_checkpoint"
    description = "Hard-reset the workspace to a previous checkpoint (discards later changes). Requires approval."
    Params = RestoreParams
    category = "destructive"
    mutates = True

    async def run(self, ctx, a: RestoreParams) -> ToolResult:
        with session_scope() as s:
            cp = s.get(Checkpoint, a.checkpoint_id)
            commit = cp.commit_hash if cp and cp.project_id == ctx.project_id else a.checkpoint_id
        async with ctx.services.lock(ctx.project_id, "git"):
            try:
                await gitutil.restore(ctx.root, commit)
            except RuntimeError as e:
                raise ToolError(str(e))
        return ToolResult(True, f"Workspace restored to {commit[:10]}.", {"commit": commit}, files_changed=["*"], summary=f"restored {commit[:10]}")
