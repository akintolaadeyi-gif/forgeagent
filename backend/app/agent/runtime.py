"""Agent runtime: run handles, approvals, pause / resume / cancel, startup recovery."""
from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..app_settings import AppSettings, load_settings
from ..db import session_scope
from ..events import bus
from ..llm.gateway import LLMCancelled, LLMError, LLMGateway
from ..models import TERMINAL_STATUSES, Message, Project, Task, TaskStatus
from ..tools.services import Services


class RunConflict(Exception):
    pass


@dataclass
class PendingApproval:
    id: str
    project_id: str
    task_id: str | None
    tool: str
    arguments: dict
    reasons: list[str]
    future: asyncio.Future
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def public(self) -> dict:
        return {"id": self.id, "project_id": self.project_id, "task_id": self.task_id, "tool": self.tool,
                "arguments": self.arguments, "reasons": self.reasons, "created": self.created}


class RunHandle:
    def __init__(self, project_id: str, kind: str, root_task_id: str | None = None) -> None:
        self.project_id = project_id
        self.kind = kind  # agent | chat
        self.root_task_id = root_task_id
        self.cancel = asyncio.Event()
        self.gate = asyncio.Event()  # set = running, cleared = paused
        self.gate.set()
        self.task: asyncio.Task | None = None
        self.current_task_id: str | None = None
        self.paused = False
        self.settings: AppSettings = load_settings()
        self.plan: dict | None = None
        self.states: dict[str, "LoopState"] = {}


@dataclass
class LoopState:
    world_version: int = 0
    sig_counts: dict[str, int] = field(default_factory=dict)
    last_test: dict | None = None  # {"ok": bool, "summary": str, "version": int}
    last_build: dict | None = None
    last_fail_text: str = ""
    files_since_fail: set = field(default_factory=set)
    used_tools: bool = False


class Gateways:
    """One shared gateway (keeps learned capabilities) that is rebuilt when model settings change."""

    def __init__(self) -> None:
        self._gw: LLMGateway | None = None
        self._key: str = ""
        self.sem = asyncio.Semaphore(1)  # one inference at a time: roles share a single local model

    def get(self, settings: AppSettings) -> LLMGateway:
        key = settings.model.model_dump_json()
        if self._gw is None or key != self._key:
            self._gw = LLMGateway(settings.model, transport=self.transport)
            self._key = key
        return self._gw

    transport: Any = None  # injectable for tests


class AgentRuntime:
    def __init__(self) -> None:
        self.services = Services()
        self.gateways = Gateways()
        self.runs: dict[str, RunHandle] = {}
        self.approvals: dict[str, PendingApproval] = {}

    # ------------------------------------------------------------------ queries
    def is_running(self, project_id: str) -> bool:
        h = self.runs.get(project_id)
        return bool(h and h.task and not h.task.done())

    def run_info(self, project_id: str) -> dict:
        h = self.runs.get(project_id)
        active = self.is_running(project_id)
        return {"active": active, "kind": h.kind if h else None, "paused": bool(h and h.paused and active),
                "task_id": h.current_task_id if h else None, "root_task_id": h.root_task_id if h else None,
                "pending_approvals": [a.public() for a in self.approvals.values() if a.project_id == project_id]}

    # ------------------------------------------------------------------ control
    def pause(self, project_id: str) -> bool:
        h = self.runs.get(project_id)
        if not h or not self.is_running(project_id):
            return False
        h.paused = True
        h.gate.clear()
        bus.publish(project_id, "paused", {}, h.current_task_id)
        return True

    def unpause(self, project_id: str) -> bool:
        h = self.runs.get(project_id)
        if not h or not self.is_running(project_id) or not h.paused:
            return False
        h.paused = False
        h.gate.set()
        bus.publish(project_id, "resumed", {}, h.current_task_id)
        return True

    async def cancel(self, project_id: str) -> bool:
        h = self.runs.get(project_id)
        if not h or not self.is_running(project_id):
            return False
        h.cancel.set()
        h.gate.set()  # release a paused loop so it can observe the cancellation
        for a in list(self.approvals.values()):
            if a.project_id == project_id and not a.future.done():
                a.future.set_result("cancelled")
        self.services.devservers.stop_all(project_id)
        assert h.task is not None
        try:
            await asyncio.wait_for(asyncio.shield(h.task), 20)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass
        return True

    async def request_approval(self, h: RunHandle, task_id: str | None, tool: str, args: dict, reasons: list[str]) -> str:
        ap = PendingApproval(uuid.uuid4().hex[:12], h.project_id, task_id, tool, args, reasons,
                             asyncio.get_running_loop().create_future())
        self.approvals[ap.id] = ap
        bus.publish(h.project_id, "approval_required", ap.public(), task_id)
        try:
            while not ap.future.done():
                if h.cancel.is_set():
                    return "cancelled"
                await asyncio.wait([ap.future], timeout=0.5)
            return ap.future.result()
        finally:
            self.approvals.pop(ap.id, None)
            bus.publish(h.project_id, "approval_resolved", {"id": ap.id, "result": ap.future.result() if ap.future.done() else "cancelled"}, task_id)

    def resolve_approval(self, approval_id: str, approve: bool) -> bool:
        ap = self.approvals.get(approval_id)
        if not ap or ap.future.done():
            return False
        ap.future.set_result("approved" if approve else "denied")
        return True

    # ------------------------------------------------------------------ recovery
    def recover_interrupted(self) -> int:
        """Called at startup: anything that was mid-flight is parked as BLOCKED so it can be resumed."""
        n = 0
        with session_scope() as s:
            for t in s.query(Task).filter(Task.status.in_((TaskStatus.PLANNING, TaskStatus.RUNNING, TaskStatus.TESTING))).all():
                t.status = TaskStatus.BLOCKED
                t.error = "Interrupted by application restart. Resume to continue from the saved state."
                n += 1
        return n

    # ------------------------------------------------------------------ helpers
    def set_status(self, task_id: str, status: TaskStatus, *, error: str | None = None, result: str | None = None) -> None:
        pid = None
        with session_scope() as s:
            t = s.get(Task, task_id)
            if not t:
                return
            t.status = status
            if error is not None:
                t.error = error
            if result is not None:
                t.result = result
            pid = t.project_id
            title, parent = t.title, t.parent_id
            proj = s.get(Project, pid)
            if proj:
                proj.last_activity = datetime.now(timezone.utc)
        bus.publish(pid, "task_status", {"task_id": task_id, "status": status.value, "title": title,
                                         "parent_id": parent, "error": error or ""}, task_id)

    def add_message(self, project_id: str, task_id: str | None, role: str, content: str, *, agent_role: str | None = None,
                    tool_calls: list | None = None, tool_call_id: str | None = None, tool_name: str | None = None,
                    visible: bool = True) -> int:
        from ..llm.gateway import estimate_tokens

        with session_scope() as s:
            m = Message(project_id=project_id, task_id=task_id, role=role, agent_role=agent_role, content=content,
                        tool_calls=tool_calls, tool_call_id=tool_call_id, tool_name=tool_name, visible=visible,
                        token_estimate=estimate_tokens(content or ""))
            s.add(m)
            s.flush()
            return m.id


PLAN_HINT = re.compile(r"\b(build|create|develop|make|generate|implement|scaffold|set ?up)\b.{0,80}\b(app|application|website|site|service|api|system|platform|project|dashboard|tool|backend|frontend|full[- ]?stack)\b", re.I | re.S)


def should_plan(goal: str, mode: str) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    return len(goal) > 400 or bool(PLAN_HINT.search(goal))


__all__ = ["AgentRuntime", "RunHandle", "LoopState", "RunConflict", "should_plan", "TERMINAL_STATUSES", "LLMCancelled", "LLMError"]
