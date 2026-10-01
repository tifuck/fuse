"""Read-only QA-browser regression for recap rendering and controls on fuse-dev.

Run with /root/fuse/venv/bin/python tests/chat_metadata_browser.py.
Snapshot metadata and writes are intercepted in the browser; no model is called.
"""
import asyncio
import argparse
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://127.0.0.1:8089"
TEXT = "Fixed token validation; tests passed. <img src=x onerror=alert(1)> remains plain text."


async def main(screenshots=False):
    login = json.loads((ROOT / "data/dev-qa-login.json").read_text())
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        for attempt in range(50):
            try:
                await page.goto(BASE + "/login")
                break
            except Exception:
                if attempt == 49:
                    raise
                await asyncio.sleep(0.2)
        await page.fill("input[name=email]", login["email"])
        await page.fill("input[name=password]", login["password"])
        await page.click("button[type=submit]")
        await page.wait_for_url("**/chat**")
        sync = await page.evaluate("fetch('/chat/sync').then(r => r.json())")
        tid = next(t["id"] for t in sync["threads"] if t["title"] == "QA · Tools, subagents and commands")
        preference_writes = []
        manual_writes = []
        unexpected_writes = []
        send_started, release_send = asyncio.Event(), asyncio.Event()

        async def readonly(route):
            if route.request.method not in ("GET", "HEAD", "OPTIONS"):
                unexpected_writes.append(urlsplit(route.request.url).path)
                return await route.abort()
            await route.continue_()

        async def snapshot(route):
            if route.request.method != "GET":
                unexpected_writes.append(urlsplit(route.request.url).path)
                return await route.abort()
            url = urlsplit(route.request.url)
            query = [(k, v) for k, v in parse_qsl(url.query) if k not in ("observed_cursor", "observed_thread_cursor")]
            response = await route.fetch(url=urlunsplit(url._replace(query=urlencode(query))))
            lines = []
            for line in (await response.text()).splitlines():
                data = json.loads(line)
                last = next(m for m in reversed(data["messages"]) if m["role"] == "assistant")
                data["recaps"] = [{"anchor_id": last["id"], "text": TEXT}]
                lines.append(json.dumps(data))
            await route.fulfill(response=response, body="\n".join(lines) + "\n")

        async def preference(route):
            if route.request.method != "PUT":
                return await route.continue_()
            data = route.request.post_data_json
            preference_writes.append(data)
            current = await page.evaluate("JSON.parse(document.getElementById('chat-config').textContent).context")
            current.update(recaps=data["recaps"], revision=data["expected_revision"] + 1)
            await route.fulfill(json=current)

        async def manual(route):
            manual_writes.append(route.request.method)
            await route.fulfill(json={"started": True, "idle_seconds": 60})

        async def delayed_send(route):
            send_started.set()
            await release_send.wait()
            await route.fulfill(status=400, json={"detail": "Read-only browser test: message not persisted"})

        await page.route("**/*", readonly)
        await page.route(re.compile(r".*/chat/threads/[A-Za-z0-9]+(?:\?.*)?$"), snapshot)
        await page.route("**/chat/context", preference)
        await page.route("**/chat/threads/*/recap", manual)
        await page.route("**/chat/threads/*/turns", delayed_send)
        for width, height in ((1440, 900), (390, 844)):
            await page.set_viewport_size({"width": width, "height": height})
            for view in ("cli", "console", "compact", "mainframe"):
                await page.evaluate("view => localStorage.setItem('fuse.chat.view.v2', view)", view)
                await page.goto(f"{BASE}/chat?t={tid}")
                await page.wait_for_selector(".chat-recap")
                recap = page.locator(".chat-recap")
                assert await recap.count() == 1
                assert await recap.inner_text() == "Recap: " + TEXT
                assert await recap.locator("img").count() == 0
                assert await page.locator("[data-chat-log] > .chat-recap").count() == 0
                assert await recap.evaluate("e => e.parentElement.matches('article.is-assistant')")
                assert await recap.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
                if screenshots:
                    await page.screenshot(path=f"/tmp/opencode/recap-{view}-{width}.png")
                # Appearance transitions and repainting must not duplicate it.
                await page.evaluate("document.querySelector('[data-chat-input]').focus()")
                assert await recap.count() == 1
                print(f"recap render: {view} {width}x{height} passed")
                # New-chat clicks hide it synchronously, before async saves.
                hidden = await page.evaluate("""() => {
                    document.querySelector('[data-chat-new]').click();
                    return document.querySelectorAll('.chat-recap').length === 0;
                }""")
                assert hidden
                await page.goto(f"{BASE}/chat?t={tid}")
                await page.wait_for_selector(".chat-recap")
                await page.wait_for_selector(".chat-sync[data-sync-state='live']", state="attached")
                send_started.clear()
                release_send.clear()
                await page.fill("[data-chat-input]", "Please continue")
                hidden = await page.evaluate("""() => {
                    document.querySelector('[data-chat-form]').requestSubmit();
                    return document.querySelectorAll('.chat-recap').length === 0;
                }""")
                assert hidden  # No animation frame or HTTP response required.
                await asyncio.wait_for(send_started.wait(), 10)
                assert await page.locator(".chat-recap").count() == 0
                release_send.set()
                await page.wait_for_selector(".chat-pending-send", state="detached")
                assert await page.locator(".chat-recap").count() == 0
                print(f"instant dismissal: {view} {width}x{height} passed")
        await page.evaluate("document.querySelector('[data-recaps-toggle]').click()")
        await page.wait_for_function("() => !document.querySelector('[data-recaps-toggle]').disabled")
        assert preference_writes and isinstance(preference_writes[-1]["recaps"], bool)
        await page.fill("[data-chat-input]", "/recap")
        await page.press("[data-chat-input]", "Enter")
        async with page.expect_request("**/chat/threads/*/recap"):
            await page.press("[data-chat-input]", "Enter")
        assert manual_writes == ["POST"]
        assert not unexpected_writes, unexpected_writes
        assert not errors, errors
        print("preference, /recap and browser diagnostics passed")
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", action="store_true")
    asyncio.run(main(parser.parse_args().screenshots))
