# backend/app/agent/runtime.py :: AgentRuntime.recover_interrupted
def recover_interrupted(self) -> int:
    """Called at startup: anything that was mid-flight is parked as BLOCKED so it can be resumed."""
    n = 0
    with session_scope() as s:
        for t in s.query(Task).filter(Task.status.in_((TaskStatus.PLANNING, TaskStatus.RUNNING, TaskStatus.TESTING))).all():
            t.status = TaskStatus.BLOCKED
            t.error = "Interrupted by application restart. Resume to continue from the saved state."
            n += 1
    return n
