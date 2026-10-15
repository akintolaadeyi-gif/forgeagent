"""Agent-control and memory tools."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .base import Tool, ToolResult, register


class CompleteParams(BaseModel):
    status: Literal["success", "partial", "blocked"] = Field(description="success only if the work is implemented AND verified by tools")
    summary: str = Field(description="Honest report: what was done, what was verified (tests/build), what is left")


@register
class CompleteTask(Tool):
    name = "complete_task"
    description = "Finish the current task and hand the report to the user. Only claim success for things confirmed by tool results."
    Params = CompleteParams
    category = "meta"

    async def run(self, ctx, a: CompleteParams) -> ToolResult:
        return ToolResult(True, "Task report recorded.", {"complete": True, "status": a.status, "summary": a.summary}, summary=f"complete_task({a.status})")


class PlanTask(BaseModel):
    title: str
    description: str = Field(description="Concrete instructions incl. files to create/change and the acceptance check")
    role: Literal["developer", "tester", "debugger"] = "developer"
    depends_on: list[int] = Field(default_factory=list, description="0-based indexes of tasks in this list that must finish first")


class SubmitPlanParams(BaseModel):
    complexity: Literal["simple", "complex"] = Field(description="'simple' = one focused change that needs no breakdown")
    summary: str = Field(description="One-paragraph architecture / approach summary")
    tech_stack: str = Field("", description="Languages, frameworks, databases chosen")
    tasks: list[PlanTask] = Field(default_factory=list, description="Ordered milestones (empty for simple)")


@register
class SubmitPlan(Tool):
    name = "submit_plan"
    description = "Submit the project plan: architecture summary and an ordered list of milestone tasks with dependencies."
    Params = SubmitPlanParams
    category = "meta"

    async def run(self, ctx, a: SubmitPlanParams) -> ToolResult:
        n = len(a.tasks)
        for i, t in enumerate(a.tasks):
            for d in t.depends_on:
                if d < 0 or d >= n or d == i:
                    from .base import ToolError

                    raise ToolError(f"Task {i} has invalid dependency index {d}")
        return ToolResult(True, f"Plan accepted with {n} task(s).", {"plan": a.model_dump()}, summary=f"plan: {n} tasks")


class RememberParams(BaseModel):
    kind: Literal["requirement", "decision", "stack", "status", "file", "error_fix", "convention", "note"]
    title: str = Field(description="Short title")
    content: str
    pinned: bool = Field(False, description="Always include in context (use sparingly)")


@register
class Remember(Tool):
    name = "remember"
    description = "Save a durable fact about this project (decision, convention, important file, error and its fix)."
    Params = RememberParams
    category = "meta"

    async def run(self, ctx, a: RememberParams) -> ToolResult:
        from ..agent.memory import add_memory

        mid = add_memory(ctx.project_id, a.kind, a.title, a.content, pinned=a.pinned, task_id=ctx.task_id)
        return ToolResult(True, f"Saved memory #{mid}.", {"id": mid}, summary=f"remembered: {a.title}")


class RecallParams(BaseModel):
    query: str
    limit: int = Field(6, ge=1, le=20)


@register
class Recall(Tool):
    name = "recall"
    description = "Search this project's saved memory (decisions, past errors and fixes, conventions)."
    Params = RecallParams
    category = "meta"

    async def run(self, ctx, a: RecallParams) -> ToolResult:
        from ..agent.memory import search_memory

        items = search_memory(ctx.project_id, a.query, a.limit)
        out = "\n".join(f"#{i.id} [{i.kind}] {i.title}: {i.content[:400]}" for i in items) or "(nothing relevant)"
        return ToolResult(True, out, summary=f"{len(items)} memories", untrusted=True)
