import asyncio

import pytest

from app.app_settings import ModelSettings
from app.llm.gateway import LLMCancelled, LLMGateway, LLMUnavailable

TOOLS = [{"type": "function", "function": {"name": "read_file", "description": "d", "parameters": {"type": "object", "properties": {}}}}]


def gw(llm, **kw):
    return LLMGateway(ModelSettings(base_url="http://fake-llm/v1", max_retries=2, **kw), transport=llm.transport)


async def test_streaming_text_and_usage(llm):
    llm.reply("Hello there, world")
    deltas = []
    c = await gw(llm).complete([{"role": "user", "content": "hi"}], None, on_delta=lambda k, t: deltas.append(t))
    assert c.content == "Hello there, world" and "".join(deltas) == c.content
    assert c.prompt_tokens == 100 and c.completion_tokens == 20 and c.tokens_per_second == 42.0


async def test_native_tool_call_assembled_from_chunks(llm):
    llm.reply("", [("read_file", {"path": "a.py", "start_line": 3})])
    c = await gw(llm).complete([{"role": "user", "content": "x"}], TOOLS)
    assert c.tool_calls[0].name == "read_file" and c.tool_calls[0].arguments == {"path": "a.py", "start_line": 3}


async def test_text_tool_call_recovered(llm):
    llm.reply('ok <tool_call>{"name": "read_file", "arguments": {"path": "z"}}</tool_call>')
    c = await gw(llm).complete([{"role": "user", "content": "x"}], TOOLS)
    assert c.tool_calls and c.tool_calls[0].arguments == {"path": "z"} and "tool_call" not in c.content


async def test_fallback_when_server_rejects_tools(llm):
    llm.reject_tools_once = True
    llm.reply('<tool_call>{"name": "read_file", "arguments": {}}</tool_call>')
    g = gw(llm)
    c = await g.complete([{"role": "system", "content": "s"}, {"role": "user", "content": "x"}], TOOLS)
    assert c.tool_calls and not c.native_tools_used
    assert "Tools" in llm.requests[-1]["messages"][0]["content"] and "tools" not in llm.requests[-1]
    assert g._native_tools_ok is False


async def test_retry_on_server_error_then_success(llm):
    llm.fail_with = [503, 500]
    llm.reply("finally")
    c = await gw(llm).complete([{"role": "user", "content": "x"}], None)
    assert c.content == "finally"


async def test_unavailable_after_retries(llm):
    llm.fail_with = [500, 500, 500, 500]
    with pytest.raises(LLMUnavailable):
        await gw(llm).complete([{"role": "user", "content": "x"}], None)


async def test_cancel(llm):
    llm.reply("a" * 500)
    ev = asyncio.Event()
    ev.set()
    with pytest.raises(LLMCancelled):
        await gw(llm).complete([{"role": "user", "content": "x"}], None, cancel=ev)


async def test_health_reports_capabilities(llm):
    h = await gw(llm).health()
    assert h["connected"] and h["model"] == "fake-model" and h["capabilities"]["n_ctx"] == 16384


async def test_cancel_is_immediate_while_waiting_for_first_token():
    """llama.cpp may spend a long time on prompt evaluation before streaming; Stop must not wait for it."""
    import time

    import httpx

    async def slow(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m"}]})

        async def body():
            await asyncio.sleep(30)  # "prompt processing"
            yield b"data: [DONE]\n\n"
        return httpx.Response(200, content=body(), headers={"content-type": "text/event-stream"})

    g = LLMGateway(ModelSettings(base_url="http://x/v1", max_retries=0), transport=httpx.MockTransport(slow))
    ev = asyncio.Event()
    asyncio.get_running_loop().call_later(0.3, ev.set)
    t0 = time.monotonic()
    with pytest.raises(LLMCancelled):
        await g.complete([{"role": "user", "content": "x"}], None, cancel=ev)
    assert time.monotonic() - t0 < 3
