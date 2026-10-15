"""Local LLM gateway: OpenAI-compatible endpoints (llama.cpp first)."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from ..app_settings import ModelSettings
from .toolparse import ParsedCall, extract_text_tool_calls, normalize_native_calls, split_thinking


class LLMError(Exception):
    pass


class LLMCancelled(LLMError):
    pass


class LLMUnavailable(LLMError):
    pass


@dataclass
class Completion:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ParsedCall] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_per_second: float = 0.0
    duration_ms: int = 0
    native_tools_used: bool = False
    model: str = ""

    def assistant_message(self) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [c.to_openai() for c in self.tool_calls if c.name]
        return msg


DeltaCb = Callable[[str, str], Awaitable[None] | None]  # (kind: "content"|"reasoning", text)


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~3.6 chars/token for code-heavy English)."""
    return max(1, int(len(text) / 3.6)) if text else 0


def estimate_messages_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += 6 + estimate_tokens(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            total += estimate_tokens(json.dumps(tc.get("function", {})))
    return total


class LLMGateway:
    def __init__(self, settings: ModelSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._native_tools_ok: bool | None = None  # learned at runtime
        self._model_cache: str | None = None

    # ------------------------------------------------------------------ plumbing
    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.settings.base_url,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                timeout=httpx.Timeout(self.settings.request_timeout_s, connect=5.0),
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _root_url(self) -> str:
        u = self.settings.base_url
        return u[:-3] if u.endswith("/v1") else u

    # ------------------------------------------------------------------ discovery / health
    async def list_models(self) -> list[dict]:
        try:
            r = await self._http().get("/models", timeout=10)
            r.raise_for_status()
        except (httpx.HTTPError, OSError) as e:
            raise LLMUnavailable(f"Cannot reach model server at {self.settings.base_url}: {e}") from e
        data = r.json()
        return data.get("data") or data.get("models") or []

    async def resolve_model(self) -> str:
        if self.settings.model:
            return self.settings.model
        if self._model_cache:
            return self._model_cache
        models = await self.list_models()
        if not models:
            raise LLMUnavailable("Model server is up but reports no models")
        mid = models[0].get("id") or models[0].get("name") or models[0].get("model") or ""
        self._model_cache = mid
        return mid

    async def health(self) -> dict:
        """Connection status + capability detection (best effort; llama.cpp specific endpoints are optional)."""
        info: dict[str, Any] = {"connected": False, "base_url": self.settings.base_url, "models": [], "model": None,
                                "capabilities": {}, "error": None}
        try:
            models = await self.list_models()
            info["connected"] = True
            info["models"] = [m.get("id") or m.get("name") for m in models]
            info["model"] = self.settings.model or (info["models"][0] if info["models"] else None)
            if models:
                meta = models[0].get("meta") or {}
                info["capabilities"]["n_ctx_train"] = meta.get("n_ctx_train")
                info["capabilities"]["n_params"] = meta.get("n_params")
                info["capabilities"]["size_bytes"] = meta.get("size")
        except LLMUnavailable as e:
            info["error"] = str(e)
            return info
        try:  # llama.cpp /props
            async with httpx.AsyncClient(timeout=5, transport=self._transport) as c:
                r = await c.get(self._root_url() + "/props")
                if r.status_code == 200:
                    p = r.json()
                    dgs = p.get("default_generation_settings", {})
                    info["capabilities"]["n_ctx"] = dgs.get("n_ctx") or p.get("n_ctx")
                    info["capabilities"]["backend"] = "llama.cpp"
                    tmpl = p.get("chat_template") or ""
                    info["capabilities"]["chat_template_supports_tools"] = ("tools" in tmpl) or ("tool_call" in tmpl)
                    info["capabilities"]["build_info"] = p.get("build_info")
        except (httpx.HTTPError, ValueError):
            pass
        info["capabilities"]["native_tools_verified"] = self._native_tools_ok
        return info

    async def count_tokens(self, text: str) -> int:
        """Exact token count through llama.cpp /tokenize when available, estimate otherwise."""
        try:
            async with httpx.AsyncClient(timeout=3, transport=self._transport) as c:
                r = await c.post(self._root_url() + "/tokenize", json={"content": text})
                if r.status_code == 200:
                    return len(r.json().get("tokens", []))
        except (httpx.HTTPError, ValueError):
            pass
        return estimate_tokens(text)

    # ------------------------------------------------------------------ completion
    def _payload(self, messages: list[dict], tools: list[dict] | None, model: str, max_tokens: int | None) -> dict:
        s = self.settings
        p: dict[str, Any] = {
            "model": model, "messages": messages, "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": s.temperature, "top_p": s.top_p,
            "max_tokens": max_tokens or s.max_tokens,
            "top_k": s.top_k, "min_p": s.min_p, "repeat_penalty": s.repeat_penalty,
        }
        if tools:
            p["tools"] = tools
            p["tool_choice"] = "auto"
        return p

    async def complete(self, messages: list[dict], tools: list[dict] | None = None, *,
                       cancel: asyncio.Event | None = None, on_delta: DeltaCb | None = None,
                       max_tokens: int | None = None) -> Completion:
        """Stream a chat completion with retry, cancellation and tool-call recovery."""
        known = {t["function"]["name"] for t in (tools or [])}
        use_native = bool(tools) and self.settings.native_tools and self._native_tools_ok is not False
        msgs = self._prepare_messages(messages, tools if (tools and not use_native) else None)
        if self.settings.disable_thinking and msgs and msgs[-1]["role"] == "user" and "/no_think" not in (msgs[-1].get("content") or ""):
            msgs = msgs[:-1] + [{**msgs[-1], "content": (msgs[-1].get("content") or "") + " /no_think"}]
        model = await self.resolve_model()

        last_err: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            if cancel and cancel.is_set():
                raise LLMCancelled("cancelled")
            try:
                comp = await self._stream_once(msgs, tools if use_native else None, model, cancel, on_delta, max_tokens)
                comp.native_tools_used = use_native
                self._finalize(comp, known, use_native)
                if use_native and comp.tool_calls:
                    self._native_tools_ok = True
                return comp
            except LLMCancelled:
                raise
            except _BadRequest as e:
                if use_native:  # server/template rejected tools -> fall back to prompt-based tool calling
                    self._native_tools_ok = False
                    use_native = False
                    msgs = self._prepare_messages(messages, tools)
                    last_err = e
                    continue
                raise LLMError(str(e)) from e
            except (httpx.TransportError, _ServerError, OSError) as e:
                last_err = e
                if attempt >= self.settings.max_retries:
                    break
                await asyncio.sleep(min(2 ** attempt, 8))
        raise LLMUnavailable(f"Model request failed after retries: {last_err}")

    def _prepare_messages(self, messages: list[dict], tools_in_prompt: list[dict] | None) -> list[dict]:
        """For the prompt-based fallback, tool schemas go into the system prompt and tool results become user turns."""
        msgs = [dict(m) for m in messages]
        if not tools_in_prompt:
            return msgs
        spec = json.dumps([t["function"] for t in tools_in_prompt], ensure_ascii=False)
        instructions = (
            "\n\n# Tools\nYou can call tools. To call one, output exactly:\n"
            '<tool_call>\n{"name": "<tool name>", "arguments": {...}}\n</tool_call>\n'
            "You may emit several <tool_call> blocks in one reply. After you call a tool, stop and wait for its result.\n"
            f"Available tools (JSON schema): {spec}"
        )
        if msgs and msgs[0]["role"] == "system":
            msgs[0]["content"] = (msgs[0].get("content") or "") + instructions
        else:
            msgs.insert(0, {"role": "system", "content": instructions.strip()})
        out: list[dict] = []
        for m in msgs:
            if m["role"] == "tool":
                out.append({"role": "user", "content": f"<tool_response name=\"{m.get('name', '')}\">\n{m.get('content', '')}\n</tool_response>"})
            elif m["role"] == "assistant" and m.get("tool_calls"):
                blocks = "\n".join(
                    "<tool_call>\n" + json.dumps({"name": tc["function"]["name"],
                                                   "arguments": _maybe_json(tc["function"]["arguments"])}) + "\n</tool_call>"
                    for tc in m["tool_calls"])
                out.append({"role": "assistant", "content": ((m.get("content") or "") + "\n" + blocks).strip()})
            else:
                out.append(m)
        return out

    async def _stream_once(self, msgs, tools, model, cancel, on_delta, max_tokens) -> Completion:
        comp = Completion(model=model)
        raw_calls: dict[int, dict] = {}
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        t0 = time.monotonic()
        first_tok: float | None = None
        timings: dict = {}
        usage: dict = {}
        payload = self._payload(msgs, tools, model, max_tokens)

        async with self._http().stream("POST", "/chat/completions", json=payload) as resp:
            if resp.status_code >= 500:
                body = (await resp.aread()).decode("utf-8", "replace")[:500]
                raise _ServerError(f"HTTP {resp.status_code}: {body}")
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")[:800]
                if resp.status_code in (400, 422):
                    raise _BadRequest(f"HTTP {resp.status_code}: {body}")
                raise LLMError(f"HTTP {resp.status_code}: {body}")
            async for line in _cancellable(resp.aiter_lines(), cancel):  # leaving the context closes the stream -> llama.cpp aborts
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    raise _ServerError(str(chunk["error"]))
                if chunk.get("usage"):
                    usage = chunk["usage"]
                if chunk.get("timings"):
                    timings = chunk["timings"]
                for ch in chunk.get("choices") or []:
                    d = ch.get("delta") or {}
                    if ch.get("finish_reason"):
                        comp.finish_reason = ch["finish_reason"]
                    rc = d.get("reasoning_content") or d.get("reasoning")
                    if rc:
                        reasoning_parts.append(rc)
                        if on_delta:
                            r = on_delta("reasoning", rc)
                            if asyncio.iscoroutine(r):
                                await r
                    c = d.get("content")
                    if c:
                        if first_tok is None:
                            first_tok = time.monotonic()
                        content_parts.append(c)
                        if on_delta:
                            r = on_delta("content", c)
                            if asyncio.iscoroutine(r):
                                await r
                    for tc in d.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = raw_calls.setdefault(idx, {"id": tc.get("id"), "type": "function",
                                                          "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]

        dur = time.monotonic() - t0
        comp.content = "".join(content_parts)
        comp.reasoning = "".join(reasoning_parts)
        comp.duration_ms = int(dur * 1000)
        comp.prompt_tokens = int(usage.get("prompt_tokens") or timings.get("prompt_n") or estimate_messages_tokens(msgs))
        comp.completion_tokens = int(usage.get("completion_tokens") or timings.get("predicted_n") or estimate_tokens(comp.content + comp.reasoning))
        if timings.get("predicted_per_second"):
            comp.tokens_per_second = float(timings["predicted_per_second"])
        elif comp.completion_tokens and dur > 0:
            gen_time = (time.monotonic() - first_tok) if first_tok else dur
            comp.tokens_per_second = comp.completion_tokens / max(gen_time, 1e-3)
        if raw_calls:
            comp.tool_calls = normalize_native_calls([raw_calls[i] for i in sorted(raw_calls)])
        return comp

    def _finalize(self, comp: Completion, known: set[str], native: bool) -> None:
        visible, reasoning = split_thinking(comp.content)
        if reasoning and not comp.reasoning:
            comp.reasoning = reasoning
        comp.content = visible
        if known:
            # drop calls to nonexistent tools only by flagging them; orchestrator reports the error back to the model
            for c in comp.tool_calls:
                if c.name and c.name not in known and not c.error:
                    c.error = f"unknown tool '{c.name}'"
            if not comp.tool_calls:  # recovery path: tool call emitted as text
                calls, rest = extract_text_tool_calls(comp.content, known)
                if calls:
                    comp.tool_calls = calls
                    comp.content = rest


async def _cancellable(aiter, cancel: asyncio.Event | None):
    """Yield from `aiter`, but raise LLMCancelled the moment `cancel` is set - even while blocked waiting for the
    first token (llama.cpp can spend a long time evaluating the prompt before it streams anything)."""
    if cancel is None:
        async for item in aiter:
            yield item
        return
    it = aiter.__aiter__()
    cancel_task = asyncio.ensure_future(cancel.wait())
    try:
        while True:
            nxt = asyncio.ensure_future(it.__anext__())
            done, _ = await asyncio.wait({nxt, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
            if cancel_task in done and nxt not in done:
                nxt.cancel()
                raise LLMCancelled("cancelled")
            try:
                item = nxt.result()
            except StopAsyncIteration:
                return
            yield item
            if cancel.is_set():
                raise LLMCancelled("cancelled")
    finally:
        cancel_task.cancel()


def _maybe_json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


class _BadRequest(LLMError):
    pass


class _ServerError(LLMError):
    pass
