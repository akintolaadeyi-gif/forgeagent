"""In-process event bus: persists timeline events and fans them out to WebSocket subscribers."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .db import session_scope
from .models import Event


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, project_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._subs[project_id].add(q)
        return q

    def unsubscribe(self, project_id: str, q: asyncio.Queue) -> None:
        self._subs[project_id].discard(q)

    def publish(self, project_id: str, type_: str, data: dict[str, Any] | None = None,
                task_id: str | None = None, persist: bool = True) -> dict:
        data = data or {}
        ev_id = None
        ts = datetime.now(timezone.utc)
        if persist:
            try:
                with session_scope() as s:
                    ev = Event(project_id=project_id, task_id=task_id, type=type_, data=data, created_at=ts)
                    s.add(ev)
                    s.flush()
                    ev_id = ev.id
            except Exception:  # never let the timeline break the agent
                pass
        payload = {"id": ev_id, "project_id": project_id, "task_id": task_id, "type": type_,
                   "data": data, "ts": ts.isoformat()}
        for q in list(self._subs.get(project_id, ())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass
        return payload


bus = EventBus()
