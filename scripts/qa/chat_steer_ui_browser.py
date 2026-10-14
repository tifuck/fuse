"""Browser regression: steer-split rendering and dock clock in Console view.

Run against the isolated fixture (tests/chat_browser_fixture.py, port 8099):

    FUSE_CHAT_TEST_MODE=1 ./venv/bin/python tests/chat_browser_fixture.py &
    /root/fuse/venv/bin/python scripts/qa/chat_steer_ui_browser.py

Send now interrupts the running reply (it no longer steers), but the /steer API
still splits a live reply, and the UI must render that split cleanly. Covers the
three reported artifacts:
1. dock clock resets on reload while a force-sent steer is still undelivered
   (a long command keeps the run busy): the served continuation elapsedMs must
   advance, so the reloaded clock continues instead of restarting at 0s.
2. reading-position jump when a steer response's frozen prefix is behind the
   streamed client (durable row lags the patch stream): the in-place re-key
   must still run and the viewport must hold.
3. no +/-1px scroll oscillation while a message sits queued in the in-flow
   Console dock.
"""
import asyncio
import json
import re
import sys

from playwright.async_api import async_playwright

ORIGIN = "http://127.0.0.1:8099"
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome"


def clock_seconds(label):
    m = re.search(r"\((?:(\d+)h )?(?:(\d+)m )?(\d+)s", label or "")
    if not m:
        return None
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


async def main():
    failures = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"], executable_path=CHROME)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        await page.goto(ORIGIN)
        await page.wait_for_function("document.querySelector('[data-sync-label]')?.textContent === 'Live sync'")
        await page.wait_for_timeout(300)
        inp = page.locator("[data-chat-input]")

        async def send(text):
            await inp.fill(text)
            await page.locator("[data-chat-send]").click()

        async def idle():
            await page.wait_for_function("!document.body.classList.contains('is-streaming')", timeout=30000)

        async def state():
            return await page.evaluate(
                "({s: document.body.classList.contains('is-streaming'), q: document.querySelectorAll('.chat-queue-item').length})"
            )

        async def settle():
            # No streaming run and an empty queue, waiting out any drain that
            # a finished turn starts from leftover queue items.
            for _ in range(8):
                await page.wait_for_timeout(400)
                s = await state()
                if not s["s"] and not s["q"]:
                    return
                if s["s"]:
                    await idle()
            raise AssertionError("thread did not settle idle with an empty queue")

        async def finish_run():
            tid = await page.evaluate("new URL(location.href).searchParams.get('t')")
            rid = await page.evaluate(
                "async id => (await (await fetch('/chat/threads/' + id)).json()).run?.id || ''", tid
            )
            if rid:
                await page.request.post(f"{ORIGIN}/__fixture/finish/{rid}")

        async def api_steer(text):
            # Send now interrupts instead of steering; the /steer API still
            # splits a live reply and the UI must render that split.
            await page.evaluate(
                """async text => {
                  const id = new URL(location.href).searchParams.get('t');
                  await fuseToolFixture.api('/chat/threads/' + id + '/steer', {
                    method: 'POST',
                    body: JSON.stringify({ text, command_id: crypto.randomUUID() }),
                  });
                }""",
                text,
            )

        # A scrollable transcript first.
        for i in range(10):
            await send(f"Seed history {i} please")
            await page.wait_for_function("document.body.classList.contains('is-streaming')", timeout=15000)
            await idle()
        await settle()

        # ---- 3: queued message, viewport pixel-stable in the in-flow dock ----
        # ---- 1: dock clock continues across reload with an undelivered steer ----
        await send("Queue fixture hold-steer clock")
        await page.wait_for_function(
            "document.body.classList.contains('is-streaming') && !document.querySelector('[data-live-dock]').hidden",
            timeout=15000,
        )
        await page.wait_for_timeout(2000)
        await send("Queued instruction one")
        await page.wait_for_function(
            "[...document.querySelectorAll('.chat-queue-item .chat-queue-text')].some(el => el.textContent.includes('Queued instruction one'))",
            timeout=5000,
        )

        # 3: with the queue open in the in-flow dock, a paused reader's
        # viewport must be pixel-stable.
        await page.evaluate(
            """() => {
              const thread = document.querySelector('[data-chat-thread]');
              thread.scrollTop = Math.max(0, thread.scrollTop - 80);
            }"""
        )
        await page.wait_for_timeout(250)
        tops = await page.evaluate(
            """async () => {
              const thread = document.querySelector('[data-chat-thread]');
              const out = [];
              for (let i = 0; i < 16; i++) { out.push(thread.scrollTop); await new Promise(r => setTimeout(r, 150)); }
              return out;
            }"""
        )
        if max(tops) - min(tops) > 1:
            failures.append(f"queued-state viewport drifted {max(tops) - min(tops):.2f}px: {tops}")

        await api_steer("Clock probe instruction")
        await page.wait_for_function(
            "[...document.querySelectorAll('.chat-turn.is-user')].some(el => el.textContent.includes('Clock probe instruction'))",
            timeout=8000,
        )
        # The hold keeps the steer undelivered; sample the dock clock mid-window.
        await page.wait_for_timeout(3000)
        label = await page.evaluate("document.querySelector('[data-live-dock-label]')?.textContent || ''")
        pre_clock = clock_seconds(label)
        await page.reload()
        await page.wait_for_function(
            "document.body.classList.contains('is-streaming') && !document.querySelector('[data-live-dock]').hidden",
            timeout=15000,
        )
        label2 = await page.evaluate("document.querySelector('[data-live-dock-label]')?.textContent || ''")
        post_clock = clock_seconds(label2)
        if pre_clock is None or post_clock is None:
            failures.append(f"no dock clock label: {label!r} -> {label2!r}")
        elif post_clock < 2 or post_clock < pre_clock - 2:
            failures.append(f"dock clock reset on reload: {pre_clock}s -> {post_clock}s ({label2!r})")
        else:
            print(f"PASS clock continues across reload: {pre_clock}s -> {post_clock}s")
        await finish_run()
        await idle()
        await settle()

        # ---- 2: a steer whose frozen prefix is STALE behind the streamed client ----
        # The served projection can lag the patch stream (state writes are
        # throttled); truncate it to a far-behind durable row. The in-place
        # re-key must still run and the viewport must hold.
        await send("Queue fixture hold-steer console")
        await page.wait_for_function(
            "document.body.classList.contains('is-streaming') && document.querySelector('[data-chat-log]').textContent.includes('Working before')",
            timeout=15000,
        )
        await page.wait_for_timeout(3500)
        # The property is about a paused reader: hold the position explicitly
        # instead of relying on bottom-following to compensate for growth.
        await page.evaluate("fuseToolFixture.follow(false)")
        assert await page.evaluate("fuseToolFixture.scrollState().following") is False
        # Anchor the reading position on the live reply itself.
        anchored = await page.evaluate(
            """() => {
              const thread = document.querySelector('[data-chat-thread]');
              const live = [...document.querySelectorAll('.chat-turn.is-assistant')].at(-1);
              live.setAttribute('data-probe', 'orig');
              const edge = thread.getBoundingClientRect().top;
              thread.scrollTop += live.getBoundingClientRect().top - edge - 12;
              return { top: thread.scrollTop, len: live.textContent.length, mid: live.dataset.messageId,
                       rects: [...document.querySelector('[data-chat-log]').children].map(el => ({ id: el.dataset.messageId, top: Math.round(el.getBoundingClientRect().top), h: Math.round(el.getBoundingClientRect().height), hidden: Boolean(el.hidden) })) };
            }"""
        )
        tid = await page.evaluate("new URL(location.href).searchParams.get('t')")

        async def truncate_snapshot(route):
            response = await route.fetch()
            body = await response.text()
            run = ""
            out = []
            for line in body.splitlines():
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    out.append(line)
                    continue
                if data.get("partial"):
                    out.append(line)
                    continue
                run = (data.get("run") or {}).get("message_id") or run
                for msg in data.get("messages") or []:
                    if msg.get("role") == "assistant" and msg.get("tools_live") and msg.get("id") != run:
                        # Simulate a durable row far behind the patch stream.
                        msg["content"] = (msg.get("content") or "")[:20]
                        msg["segments"] = []
                out.append(json.dumps(data))
            await route.fulfill(response=response, body="\n".join(out) + "\n")

        await page.route(re.compile(rf"/chat/threads/{re.escape(tid)}(\?|$)"), truncate_snapshot, times=1)
        await api_steer("Stale snapshot steer")
        await page.wait_for_function(
            "[...document.querySelectorAll('.chat-turn.is-user')].some(el => el.textContent.includes('Stale snapshot steer'))",
            timeout=8000,
        )
        await page.wait_for_timeout(600)
        after = await page.evaluate(
            """([mid]) => {
              const thread = document.querySelector('[data-chat-thread]');
              const user = [...document.querySelectorAll('.chat-turn.is-user')].find(el => el.textContent.includes('Stale snapshot steer'));
              const probe = document.querySelector('[data-probe]');
              const frozen = user ? [...user.parentElement.children].filter(el => el.classList.contains('is-assistant') && el.compareDocumentPosition(user) & Node.DOCUMENT_POSITION_FOLLOWING).pop() : null;
              const cont = user ? user.nextElementSibling : null;
              return {
                top: thread.scrollTop,
                rects: [...document.querySelector('[data-chat-log]').children].map(el => ({ id: el.dataset.messageId, top: Math.round(el.getBoundingClientRect().top), h: Math.round(el.getBoundingClientRect().height), hidden: Boolean(el.hidden) })),
                probeRekeyedBeforeUser: Boolean(probe && user && frozen === probe && probe.dataset.messageId !== mid && probe.compareDocumentPosition(user) & Node.DOCUMENT_POSITION_FOLLOWING),
                probeKeptText: Boolean(probe && probe.textContent.includes('Working before')),
                contHasOld: cont ? cont.textContent.includes('Working before') : false,
                users: [...document.querySelectorAll('.chat-turn.is-user')].filter(el => el.textContent.includes('Stale snapshot steer')).length,
              };
            }""",
            [anchored["mid"]],
        )
        if after["users"] != 1:
            failures.append(f"steer user bubble count {after['users']} != 1")
        if not after["probeRekeyedBeforeUser"]:
            failures.append("stale snapshot skipped the in-place frozen re-key (live node ended below the steer)")
        if not after["probeKeptText"]:
            failures.append("re-keyed node lost the streamed text")
        if after["contHasOld"]:
            failures.append("old tokens leaked below the new user message")
        if abs(after["top"] - anchored["top"]) > 12:
            failures.append(
                f"viewport jumped on stale-prefix steer: {anchored['top']:.1f} -> {after['top']:.1f}\n"
                f"before: {anchored['rects']}\nafter:  {after['rects']}"
            )
        else:
            print(f"PASS stale-prefix steer holds the reading anchor ({anchored['top']:.1f} -> {after['top']:.1f})")
        await finish_run()

        if errors:
            failures.append(f"JS errors: {errors}")
        await context.close()
        await browser.close()

    if failures:
        for f in failures:
            print("FAIL", f)
        sys.exit(1)
    print("PASS steer clock/anchor/oscillation regressions")


if __name__ == "__main__":
    asyncio.run(main())
