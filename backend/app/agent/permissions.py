"""Approval policy: maps (tool, arguments, autonomy mode) -> allow / ask / deny."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from ..app_settings import AutonomySettings
from ..security import classify_command, is_secret_file
from ..tools.base import Tool, ToolContext, ToolError
from ..tools.browser_tools import is_local_url


@dataclass
class Decision:
    action: str  # allow | ask | deny
    reasons: list[str] = field(default_factory=list)


def _command_for(tool: Tool, ctx: ToolContext, args: BaseModel) -> str | None:
    n = tool.name
    if n in ("run_command", "start_dev_server"):
        return getattr(args, "command", None)
    if n in ("run_tests", "run_build"):
        try:
            return tool.resolve_command(ctx, args)  # type: ignore[attr-defined]
        except ToolError:
            return None
    return None


def _paths_for(tool: Tool, args: BaseModel) -> list[str]:
    out = []
    for f in ("path", "source", "destination", "database"):
        v = getattr(args, f, None)
        if isinstance(v, str):
            out.append(v)
    return out


def decide(tool: Tool, args: BaseModel, ctx: ToolContext) -> Decision:
    aut: AutonomySettings = ctx.settings.autonomy
    if tool.category == "meta":
        return Decision("allow")

    if aut.mode == "custom":
        pol = aut.custom_permissions.get(tool.name)
        if pol == "deny":
            return Decision("deny", [f"tool '{tool.name}' is disabled in custom permissions"])
        if pol == "ask":
            return Decision("ask", [f"approval required for '{tool.name}' (custom rule)"])
        if pol == "allow":
            return Decision("allow")
        # unspecified tools fall through to the project-autonomous policy

    if aut.mode == "supervised":
        if tool.category == "read":
            if tool.name == "read_file" and any(is_secret_file(p) for p in _paths_for(tool, args)):
                return Decision("ask", ["reads a file that may contain secrets"])
            return Decision("allow")
        if tool.category == "git" and not tool.mutates:
            return Decision("allow")
        return Decision("ask", [f"supervised mode: approval required for {tool.category} action '{tool.name}'"])

    # project_autonomous (and custom fall-through)
    reasons: list[str] = []
    if tool.category == "read":
        if tool.name == "read_file" and any(is_secret_file(p) for p in _paths_for(tool, args)):
            reasons.append("reads a file that may contain secrets")
        return Decision("ask" if reasons else "allow", reasons)
    if tool.category == "destructive":
        return Decision("ask", [f"destructive action: {tool.name}"])
    if tool.category == "write":
        if any(is_secret_file(p) for p in _paths_for(tool, args)):
            return Decision("ask", ["modifies a file that may contain secrets"])
        return Decision("allow")
    if tool.category == "exec":
        cmd = _command_for(tool, ctx, args)
        if cmd:
            verdict, why = classify_command(cmd, ctx.root, aut)
            if verdict == "approval":
                return Decision("ask", why)
        return Decision("allow")
    if tool.category == "browser":
        url = getattr(args, "url", None)
        if url and not is_local_url(url):
            return Decision("ask", [f"external URL: {url}"])
        return Decision("allow")
    return Decision("allow")


__all__ = ["Decision", "decide", "Path"]
