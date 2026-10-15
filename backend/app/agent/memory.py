"""Persistent project memory with local vector search (feature-hashed TF vectors; no external service)."""
from __future__ import annotations

import math
import re
import zlib
from collections import Counter

from ..app_settings import MemorySettings
from ..db import session_scope
from ..models import MemoryItem

DIM = 4096
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "for", "on", "with", "as", "at", "by", "be", "this", "that"}

KINDS = ["requirement", "decision", "stack", "status", "completed", "pending", "error_fix", "file", "test", "convention", "note"]


def _tokens(text: str) -> list[str]:
    toks: list[str] = []
    for raw in _TOKEN.findall(text):
        for part in _CAMEL.split(raw):
            p = part.lower()
            if len(p) > 1 and p not in _STOP:
                toks.append(p)
    return toks


def embed(text: str) -> list[list[float]]:
    toks = _tokens(text)
    feats = Counter(toks) + Counter(f"{a}_{b}" for a, b in zip(toks, toks[1:]))
    vec: dict[int, float] = {}
    for f, c in feats.items():
        h = zlib.crc32(f.encode())
        idx = h % DIM
        sign = 1.0 if (h >> 16) & 1 else -1.0
        vec[idx] = vec.get(idx, 0.0) + sign * (1 + math.log(c))
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return [[i, round(v / norm, 5)] for i, v in vec.items()]


def cosine(a: list[list[float]], b: list[list[float]]) -> float:
    if not a or not b:
        return 0.0
    db = {int(i): v for i, v in b}
    return sum(v * db.get(int(i), 0.0) for i, v in a)


def add_memory(project_id: str, kind: str, title: str, content: str, *, pinned: bool = False,
               task_id: str | None = None, dedupe: bool = True) -> int:
    kind = kind if kind in KINDS else "note"
    with session_scope() as s:
        if dedupe:
            ex = s.query(MemoryItem).filter(MemoryItem.project_id == project_id, MemoryItem.kind == kind,
                                            MemoryItem.title == title[:300]).first()
            if ex:
                ex.content = content
                ex.vector = embed(f"{title} {content}")
                ex.pinned = pinned or ex.pinned
                return ex.id
        item = MemoryItem(project_id=project_id, kind=kind, title=title[:300], content=content, pinned=pinned,
                          source_task_id=task_id, vector=embed(f"{title} {content}"))
        s.add(item)
        s.flush()
        return item.id


def update_memory(item_id: int, **fields) -> bool:
    with session_scope() as s:
        item = s.get(MemoryItem, item_id)
        if not item:
            return False
        for k in ("kind", "title", "content", "pinned"):
            if k in fields and fields[k] is not None:
                setattr(item, k, fields[k])
        item.vector = embed(f"{item.title} {item.content}")
        return True


def delete_memory(item_id: int) -> bool:
    with session_scope() as s:
        item = s.get(MemoryItem, item_id)
        if not item:
            return False
        s.delete(item)
        return True


def search_memory(project_id: str, query: str, limit: int = 8) -> list[MemoryItem]:
    qv = embed(query)
    qtoks = set(_tokens(query))
    with session_scope() as s:
        items = s.query(MemoryItem).filter(MemoryItem.project_id == project_id).all()
        scored = []
        for it in items:
            sc = cosine(qv, it.vector or [])
            if qtoks:
                text = set(_tokens(f"{it.title} {it.content}"))
                sc += 0.3 * len(qtoks & text) / len(qtoks)
            if sc > 0.05:
                scored.append((sc, it))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [it for _, it in scored[:limit]]


def memory_block(project_id: str, query: str, cfg: MemorySettings, max_chars: int = 5000) -> str:
    """Text injected into the system prompt: pinned items + the most relevant others."""
    if not cfg.enabled:
        return ""
    with session_scope() as s:
        pinned = s.query(MemoryItem).filter(MemoryItem.project_id == project_id, MemoryItem.pinned.is_(True)).all()
    relevant = [m for m in search_memory(project_id, query, cfg.max_retrieved_items) if not m.pinned]
    lines: list[str] = []
    for m in pinned + relevant:
        lines.append(f"- [{m.kind}] {m.title}: {m.content.strip()[:600]}")
    text = "\n".join(lines)
    return text[:max_chars]
