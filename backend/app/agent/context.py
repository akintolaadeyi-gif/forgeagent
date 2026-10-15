"""Context window management: DB history -> model messages, token budgeting, truncation, compaction."""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..app_settings import AppSettings
from ..db import session_scope
from ..llm.gateway import LLMGateway, LLMError, estimate_messages_tokens, estimate_tokens
from ..models import Message, Task


def truncate_output(text: str, max_chars: int, run_id: int | None = None) -> str:
    """Keep head and tail of long output; the model can fetch the rest with read_full_output."""
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.6)
    tail = max_chars - head
    hint = f" Use read_full_output(tool_run_id={run_id}) for more." if run_id else ""
    return f"{text[:head]}\n...[{len(text) - max_chars} characters omitted.{hint}]...\n{text[-tail:]}"


def row_to_message(m: Message) -> dict:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id or "", "name": m.tool_name or "", "content": m.content}
    msg: dict = {"role": m.role, "content": m.content or ""}
    if m.role == "assistant" and m.tool_calls:
        msg["tool_calls"] = m.tool_calls
    return msg


def repair_tool_pairs(msgs: list[dict]) -> list[dict]:
    """Guarantee every assistant tool call is followed by a tool message (needed after interrupted runs)."""
    out: list[dict] = []
    i = 0
    while i < len(msgs):
        m = msgs[i]
        out.append(m)
        if m["role"] == "assistant" and m.get("tool_calls"):
            wanted = [tc["id"] for tc in m["tool_calls"]]
            j = i + 1
            seen: set[str] = set()
            while j < len(msgs) and msgs[j]["role"] == "tool":
                seen.add(msgs[j].get("tool_call_id", ""))
                out.append(msgs[j])
                j += 1
            for tc in m["tool_calls"]:
                if tc["id"] not in seen:
                    out.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["function"]["name"],
                                "content": "ERROR: this action was interrupted (application restart or cancellation). Its outcome is unknown - re-inspect the current state before continuing."})
            i = j
            continue
        i += 1
    return out


def recent_conversation(project_id: str, exclude_task_id: str | None, limit: int = 6, per_msg: int = 500) -> str:
    with session_scope() as s:
        q = s.query(Message).filter(Message.project_id == project_id, Message.visible.is_(True),
                                    Message.role.in_(("user", "assistant")))
        if exclude_task_id:
            q = q.filter((Message.task_id != exclude_task_id) | (Message.task_id.is_(None)))
        rows = q.order_by(Message.id.desc()).limit(limit).all()
    rows.reverse()
    return "\n".join(f"{r.role.upper()}: {(r.content or '')[:per_msg]}" for r in rows if r.content)


@dataclass
class Budget:
    context: int
    reserve: int
    tools_tokens: int

    @property
    def usable(self) -> int:
        return max(1024, self.context - self.reserve - self.tools_tokens)


def make_budget(settings: AppSettings, tool_specs: list[dict]) -> Budget:
    ctx = settings.model.context_size
    reserve = min(settings.model.max_tokens, ctx // 3)
    return Budget(ctx, reserve, estimate_tokens(json.dumps(tool_specs)))


def load_active_messages(task_id: str) -> list[Message]:
    with session_scope() as s:
        return (s.query(Message).filter(Message.task_id == task_id, Message.compacted.is_(False))
                .order_by(Message.id).all())


def _shrink_old_tool_outputs(msgs: list[dict], keep_last: int = 6, limit: int = 500) -> list[dict]:
    tool_idx = [i for i, m in enumerate(msgs) if m["role"] == "tool"]
    for i in tool_idx[:-keep_last] if keep_last else tool_idx:
        c = msgs[i]["content"]
        if len(c) > limit:
            msgs[i] = {**msgs[i], "content": c[:limit] + f"\n...[older output shortened to save context]"}
    return msgs


def fit_to_budget(system: str, history: list[dict], budget: Budget) -> list[dict]:
    """Last-resort trimming if the history still does not fit."""
    msgs = [{"role": "system", "content": system}] + history
    if estimate_messages_tokens(msgs) <= budget.usable:
        return msgs
    history = _shrink_old_tool_outputs(list(history))
    msgs = [{"role": "system", "content": system}] + history
    # drop oldest turns (keeping the first user message = the goal) until it fits
    while estimate_messages_tokens(msgs) > budget.usable and len(history) > 4:
        drop = 1
        while drop < len(history) - 2 and history[drop]["role"] == "tool":
            drop += 1
        # drop assistant + its tool results as a unit
        end = drop + 1
        while end < len(history) and history[end]["role"] == "tool":
            end += 1
        history = history[:1] + history[end:]
        msgs = [{"role": "system", "content": system}] + history
    return msgs


def heuristic_summary(rows: list[Message]) -> str:
    lines = []
    for r in rows:
        if r.role == "assistant":
            for tc in r.tool_calls or []:
                fn = tc.get("function", {})
                lines.append(f"- called {fn.get('name')}({(fn.get('arguments') or '')[:120]})")
            if r.content and not r.tool_calls:
                lines.append(f"- said: {r.content[:200]}")
        elif r.role == "tool":
            lines.append(f"  -> {(r.content or '')[:160].splitlines()[0] if r.content else ''}")
    return "\n".join(lines[-60:])


async def compact_history(gateway: LLMGateway, task_id: str, settings: AppSettings, cancel=None) -> bool:
    """Fold older messages into Task.summary. Returns True when something was compacted."""
    keep = settings.memory.keep_recent_messages
    with session_scope() as s:
        rows = (s.query(Message).filter(Message.task_id == task_id, Message.compacted.is_(False)).order_by(Message.id).all())
        task = s.get(Task, task_id)
        if len(rows) <= keep + 2 or task is None:
            return False
        cut = len(rows) - keep
        # never split an assistant tool_call from its tool results; and keep the first (goal) user message
        while cut < len(rows) and rows[cut].role == "tool":
            cut += 1
        old = [r for r in rows[:cut] if not (r.role == "user" and r is rows[0])]
        if not old:
            return False
        prior = task.summary
        transcript = "\n".join(
            f"[{r.role}{'/' + r.tool_name if r.tool_name else ''}] "
            + ((r.content or "")[:700] + (" TOOL_CALLS: " + json.dumps(r.tool_calls)[:400] if r.tool_calls else ""))
            for r in old)
        ids = [r.id for r in old]
    prompt = [
        {"role": "system", "content": "You compress an AI software-engineering agent's working log into a dense progress summary. "
                                      "Keep: the goal, decisions, files created/changed (paths), commands run and their real outcomes, "
                                      "test/build status, unresolved errors, and the next steps. Drop chatter. Max 350 words. Plain text."},
        {"role": "user", "content": (f"Previous summary:\n{prior}\n\n" if prior else "") + f"New log to fold in:\n{transcript[:14000]}"},
    ]
    try:
        comp = await gateway.complete(prompt, None, cancel=cancel, max_tokens=700)
        summary = comp.content.strip() or heuristic_summary(old)
    except LLMError:
        summary = (prior + "\n" if prior else "") + heuristic_summary(old)
    with session_scope() as s:
        task = s.get(Task, task_id)
        task.summary = summary[:6000]
        for r in s.query(Message).filter(Message.id.in_(ids)).all():
            r.compacted = True
    return True
