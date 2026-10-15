"""Tool framework: typed tools with validated inputs and structured outputs."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from ..app_settings import AppSettings


class ToolError(Exception):
    """Expected failure; message is shown to the model."""


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    files_changed: list[str] = field(default_factory=list)
    summary: str = ""
    untrusted: bool = False  # output includes third-party content (file text, web pages, logs)


@dataclass
class ToolContext:
    project_id: str
    workspace: Path
    task_id: str | None
    settings: AppSettings
    cancel: asyncio.Event
    services: "Services"  # noqa: F821  (see tools/services.py)
    agent_role: str = "developer"

    @property
    def root(self) -> Path:
        return self.workspace


class Tool:
    name: ClassVar[str]
    description: ClassVar[str]
    Params: ClassVar[type[BaseModel]]
    # read | write | exec | git | browser | destructive | meta
    category: ClassVar[str] = "read"
    mutates: ClassVar[bool] = False

    async def run(self, ctx: ToolContext, args: BaseModel) -> ToolResult:  # pragma: no cover - interface
        raise NotImplementedError

    @classmethod
    def spec(cls) -> dict:
        schema = cls.Params.model_json_schema()
        schema.pop("title", None)
        for p in schema.get("properties", {}).values():
            p.pop("title", None)
        return {"type": "function", "function": {"name": cls.name, "description": cls.description, "parameters": schema}}


REGISTRY: dict[str, Tool] = {}


def register(cls: type[Tool]) -> type[Tool]:
    REGISTRY[cls.name] = cls()
    return cls
