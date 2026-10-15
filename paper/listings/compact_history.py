# backend/app/agent/context.py :: compact_history
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
