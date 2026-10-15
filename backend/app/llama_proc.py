"""Optional management of a local llama.cpp server process (start / stop / list models)."""
from __future__ import annotations

import asyncio
import os
import subprocess
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import get_env
from .tools.proc import kill_tree


class LlamaServerManager:
    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.log: deque[str] = deque(maxlen=500)
        self.cmdline: list[str] = []
        self._pump: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def list_models(self) -> list[dict]:
        base = Path(get_env().models_dir)
        out = []
        if base.exists():
            for p in sorted(base.rglob("*.gguf")):
                if "mmproj" in p.name.lower():
                    continue
                out.append({"path": str(p), "name": p.name, "size_gb": round(p.stat().st_size / 1e9, 2)})
        return out

    def build_args(self, model_path: str, ctx: int, ngl: int, port: int, extra: str = "", kv_quant: str = "q8_0",
                   threads: int | None = None) -> list[str]:
        exe = get_env().llama_server_path
        args = [exe, "-m", model_path, "-c", str(ctx), "-ngl", str(ngl), "--host", "127.0.0.1", "--port", str(port),
                "--jinja", "-fa", "on"]
        if kv_quant and kv_quant != "f16":
            args += ["-ctk", kv_quant, "-ctv", kv_quant]
        if threads:
            args += ["-t", str(threads)]
        if extra.strip():
            args += extra.split()
        return args

    async def start(self, model_path: str, ctx: int = 16384, ngl: int | None = None, port: int | None = None,
                    extra: str = "", kv_quant: str = "q8_0") -> dict:
        if self.running:
            raise RuntimeError("llama-server is already running (stop it first).")
        exe = Path(get_env().llama_server_path)
        if not exe.exists():
            raise FileNotFoundError(f"llama-server not found at {exe}. Set FORGE_LLAMA_SERVER_PATH.")
        mp = Path(model_path)
        if not mp.exists():
            raise FileNotFoundError(f"Model file not found: {mp}")
        env = get_env()
        port = port or (urlparse(env.llm_base_url).port or 8080)
        ngl = env.llama_gpu_layers if ngl is None else ngl
        self.cmdline = self.build_args(str(mp), ctx, ngl, port, extra or env.llama_extra_args, kv_quant)
        self.log.clear()
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        self.proc = await asyncio.create_subprocess_exec(*self.cmdline, cwd=str(exe.parent), stdin=asyncio.subprocess.DEVNULL,
                                                         stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **kwargs)

        async def pump():
            assert self.proc and self.proc.stdout
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                self.log.append(line.decode("utf-8", "replace").rstrip())

        self._pump = asyncio.create_task(pump())
        return {"pid": self.proc.pid, "cmdline": self.cmdline}

    async def wait_ready(self, base_url: str, timeout_s: int = 300) -> bool:
        root = base_url[:-3] if base_url.endswith("/v1") else base_url
        async with httpx.AsyncClient(timeout=3) as c:
            for _ in range(timeout_s):
                if not self.running:
                    return False
                try:
                    r = await c.get(root + "/health")
                    if r.status_code == 200:
                        return True
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
        return False

    def stop(self) -> bool:
        if self.running and self.proc:
            kill_tree(self.proc.pid)
            return True
        return False


llama = LlamaServerManager()
