# backend/app/llm/gateway.py :: _cancellable
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
