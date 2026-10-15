"""Playwright-backed browser tools for testing locally developed apps."""
from __future__ import annotations

import time
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ..config import get_env
from .base import Tool, ToolError, ToolResult, register

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def is_local_url(url: str) -> bool:
    try:
        return (urlparse(url).hostname or "") in LOCAL_HOSTS
    except ValueError:
        return False


class OpenBrowserParams(BaseModel):
    url: str = Field(description="URL to open, normally http://localhost:<port>")
    wait_ms: int = Field(800, ge=0, le=15000, description="Extra time to let the page settle")


@register
class OpenBrowser(Tool):
    name = "open_browser"
    description = "Open a URL in a headless browser. Returns title, HTTP status, visible text and counts of console/page errors."
    Params = OpenBrowserParams
    category = "browser"

    async def run(self, ctx, a: OpenBrowserParams) -> ToolResult:
        if not a.url.startswith(("http://", "https://")):
            raise ToolError("URL must start with http:// or https://")
        try:
            sess = await ctx.services.browser.session(ctx.project_id)
        except RuntimeError as e:
            raise ToolError(str(e))
        sess.console.clear(); sess.page_errors.clear(); sess.failed_requests.clear()
        try:
            resp = await sess.page.goto(a.url, wait_until="load", timeout=30000)
            if a.wait_ms:
                await sess.page.wait_for_timeout(a.wait_ms)
        except Exception as e:
            raise ToolError(f"Navigation failed: {str(e)[:400]}")
        title = await sess.page.title()
        text = await sess.page.evaluate("() => document.body ? document.body.innerText : ''")
        errs = [c for c in sess.console if c["type"] in ("error", "warning")]
        out = (f"URL: {sess.page.url}\nStatus: {resp.status if resp else 'n/a'}\nTitle: {title}\n"
               f"Console errors/warnings: {len(errs)} | Page errors: {len(sess.page_errors)} | Failed requests: {len(sess.failed_requests)}\n"
               f"--- visible text (first 1500 chars) ---\n{text[:1500]}")
        ok = bool(resp and resp.status < 400)
        return ToolResult(ok, out, {"status": resp.status if resp else None, "console_errors": len(errs),
                                    "page_errors": len(sess.page_errors)}, summary=f"opened {a.url} ({resp.status if resp else '?'})", untrusted=True)


class ScreenshotParams(BaseModel):
    full_page: bool = False
    name: str = Field("screenshot", description="File name stem")


@register
class CaptureScreenshot(Tool):
    name = "capture_screenshot"
    description = "Capture a PNG screenshot of the current browser page. Returns the saved file path."
    Params = ScreenshotParams
    category = "browser"

    async def run(self, ctx, a: ScreenshotParams) -> ToolResult:
        try:
            sess = await ctx.services.browser.session(ctx.project_id)
        except RuntimeError as e:
            raise ToolError(str(e))
        if sess.page.url in ("about:blank", ""):
            raise ToolError("No page is open. Call open_browser first.")
        d = get_env().screenshots_dir / ctx.project_id
        d.mkdir(parents=True, exist_ok=True)
        stem = "".join(c for c in a.name if c.isalnum() or c in "-_")[:40] or "screenshot"
        path = d / f"{stem}-{int(time.time())}.png"
        await sess.page.screenshot(path=str(path), full_page=a.full_page)
        return ToolResult(True, f"Screenshot saved: {path}", {"path": str(path), "url": f"/api/projects/{ctx.project_id}/screenshots/{path.name}"},
                          summary=f"screenshot {path.name}")


class BrowserErrorsParams(BaseModel):
    clear: bool = False


@register
class InspectBrowserErrors(Tool):
    name = "inspect_browser_errors"
    description = "List console errors, uncaught page exceptions and failed/HTTP>=400 requests since the page was opened."
    Params = BrowserErrorsParams
    category = "browser"

    async def run(self, ctx, a: BrowserErrorsParams) -> ToolResult:
        try:
            sess = await ctx.services.browser.session(ctx.project_id)
        except RuntimeError as e:
            raise ToolError(str(e))
        cons = [f"[{c['type']}] {c['text']}" for c in sess.console if c["type"] in ("error", "warning")]
        out = ["Console:"] + (cons or ["  (none)"]) + ["Uncaught page errors:"] + (sess.page_errors or ["  (none)"]) + \
              ["Failed requests:"] + (sess.failed_requests[:30] or ["  (none)"])
        n = len(cons) + len(sess.page_errors) + len(sess.failed_requests)
        res = ToolResult(True, "\n".join(out), {"count": n}, summary=f"{n} browser problems", untrusted=True)
        if a.clear:
            sess.console.clear(); sess.page_errors.clear(); sess.failed_requests.clear()
        return res


class Action(BaseModel):
    type: str = Field(description="click | fill | press | wait_for | text")
    selector: str | None = None
    value: str | None = None


class InteractParams(BaseModel):
    actions: list[Action] = Field(description="Ordered UI actions; selectors are Playwright selectors (css, text=..., role=...)")


@register
class BrowserInteract(Tool):
    name = "browser_interact"
    description = "Interact with the open page (click, fill, press key, wait_for selector, read text of a selector) to test behaviour."
    Params = InteractParams
    category = "browser"

    async def run(self, ctx, a: InteractParams) -> ToolResult:
        try:
            sess = await ctx.services.browser.session(ctx.project_id)
        except RuntimeError as e:
            raise ToolError(str(e))
        log: list[str] = []
        ok = True
        for act in a.actions[:25]:
            try:
                if act.type == "click" and act.selector:
                    await sess.page.click(act.selector, timeout=5000)
                elif act.type == "fill" and act.selector is not None:
                    await sess.page.fill(act.selector, act.value or "", timeout=5000)
                elif act.type == "press" and act.value:
                    await sess.page.keyboard.press(act.value)
                elif act.type == "wait_for" and act.selector:
                    await sess.page.wait_for_selector(act.selector, timeout=8000)
                elif act.type == "text" and act.selector:
                    t = await sess.page.inner_text(act.selector, timeout=5000)
                    log.append(f"text({act.selector}) = {t[:500]!r}")
                    continue
                else:
                    raise ValueError(f"bad action {act.model_dump()}")
                log.append(f"ok: {act.type} {act.selector or act.value}")
            except Exception as e:
                ok = False
                log.append(f"FAILED: {act.type} {act.selector or act.value}: {str(e)[:200]}")
                break
        await sess.page.wait_for_timeout(300)
        return ToolResult(ok, "\n".join(log), summary=f"{len(log)} browser actions", untrusted=True)
