#!/usr/bin/env python3
"""Screenshot fuse-dev as the QA login, for visual checks.

Needs Playwright, which only the production venv has:

    /root/fuse/venv/bin/python scripts/dev_qa_shot.py --view console --out /tmp/shots
    /root/fuse/venv/bin/python scripts/dev_qa_shot.py --view cli,compact --size 390x844 --agents --live
    /root/fuse/venv/bin/python scripts/dev_qa_shot.py --view console --backdrop garden:40 --wait 8

Run from /root/fuse-dev. Logs in with data/dev-qa-login.json (created by
scripts.dev_qa_account) and opens the "QA · Tools, subagents and commands"
fixture unless --thread gives a public id. --agents opens the side pane;
--agent also opens its first subagent. --live makes the open run and the
subagents look running by rewriting their JSON in the browser only (writes are
blocked), so live docks can be checked without a model call. Prints each
file it writes plus the composer and subagent-dock boxes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://127.0.0.1:8089"
FIXTURE_TITLE = "QA · Tools, subagents and commands"
VIEWS = ("cli", "console", "compact", "mainframe")


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _live(page):
    now = datetime.now(timezone.utc)

    async def thread(route):
        if route.request.method != "GET":
            return await route.continue_()
        resp = await route.fetch()
        data = await resp.json()
        last = next((m for m in reversed(data.get("messages") or []) if m["role"] == "assistant"), None)
        if last:
            run = dict(data.get("run") or {})
            run.update(id=run.get("id") or "qa-live", status="running", message_id=last["id"], message=last,
                       created_at=_iso(now - timedelta(seconds=75)), updated_at=_iso(now), cancel_requested=False)
            data["run"] = run
        await route.fulfill(response=resp, body=json.dumps(data))

    async def runs(route):
        if route.request.method != "GET":
            return await route.abort()
        resp = await route.fetch()
        data = await resp.json()
        data.update(status="running", created_at=_iso(now - timedelta(seconds=47)),
                    updated_at=_iso(datetime.now(timezone.utc)), cancel_requested=False)
        data["compaction"] = {**(data.get("compaction") or {}),
                              "meter": {"used": 18400, "limit": 128000, "run_id": data["id"]}}
        await route.fulfill(response=resp, body=json.dumps(data))

    async def agents(route):
        resp = await route.fetch()
        data = await resp.json()
        for i, row in enumerate(data.get("agents") or []):
            if i % 2 == 0:
                row.update(status="running", current="thinking", quiet_s=2, summary="",
                           updated_at=_iso(datetime.now(timezone.utc)))
        data["live_count"] = sum(1 for row in data.get("agents") or [] if row["status"] == "running")
        await route.fulfill(response=resp, body=json.dumps(data))

    await page.route("**/chat/threads/*/agents", agents)
    await page.route(lambda u: "/chat/threads/" in u and u.split("?")[0].rstrip("/").count("/") == 5, thread)
    await page.route("**/chat/runs/**", runs)


BOXES = """() => Object.fromEntries(Object.entries({
  composer: '.chat-box', dock: '[data-live-dock]', agentDock: '[data-agent-live-dock]', agentsPane: '[data-chat-agents]',
}).map(([k, s]) => { const e = document.querySelector(s); if (!e || e.hidden) return [k, null];
  const b = e.getBoundingClientRect(); return [k, {top: Math.round(b.top), height: Math.round(b.height), width: Math.round(b.width)}]; }))"""


async def main(args):
    login = json.loads((ROOT / "data" / "dev-qa-login.json").read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    width, height = map(int, args.size.split("x"))
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height},
                                        device_scale_factor=args.scale)
        page = await ctx.new_page()
        await page.goto(BASE + "/login")
        await page.fill("input[name=email]", login["email"])
        await page.fill("input[name=password]", login["password"])
        await page.click("button[type=submit]")
        await page.wait_for_url("**/chat**")
        thread = args.thread
        if not thread:
            sync = await page.evaluate("fetch('/chat/sync').then(r => r.json())")
            thread = next(t["id"] for t in sync["threads"] if t["title"] == FIXTURE_TITLE)
        if args.live:
            await _live(page)
        for view in args.view.split(","):
            if view not in VIEWS:
                raise SystemExit(f"unknown view {view!r}; one of {', '.join(VIEWS)}")
            await page.evaluate(f"localStorage.setItem('fuse.chat.view.v2', '{view}')")
            if args.theme:
                name, _, colors = args.theme.partition(":")
                await page.evaluate("([n, c]) => { localStorage.setItem('fuse.chat.theme.v1', n); if (c) {"
                                    " const [ink, brass] = c.split(','); localStorage.setItem('fuse.chat.custom.v1',"
                                    " JSON.stringify({ink, brass})); } }", [name, colors])
            if args.pane_width:
                await page.evaluate(f"localStorage.setItem('fuse.chat.agentsWidth', '{args.pane_width}')")
            if args.backdrop:
                kind, _, strength = args.backdrop.partition(":")
                await page.evaluate("([k, s]) => { localStorage.setItem('fuse.chat.backdrop.v1', k);"
                                    " if (s) localStorage.setItem('fuse.chat.backdrop.strength.v1', s); }", [kind, strength])
            await page.goto(f"{BASE}/chat?t={thread}")
            await page.wait_for_timeout(2500 + int(args.wait * 1000))
            if args.agents or args.agent:
                await page.click("[data-agents-tab]")
                await page.wait_for_timeout(1000)
                if args.agent:
                    await page.click("[data-agents-list] button")
                    await page.wait_for_timeout(1500)
            tag = f"-{args.backdrop.partition(':')[0]}" if args.backdrop else ""
            path = out / f"{view}-{args.size}{tag}{'-live' if args.live else ''}.png"
            await page.screenshot(path=str(path), full_page=False)
            print(path, json.dumps(await page.evaluate(BOXES)))
        await browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--view", default=",".join(VIEWS), help="comma list of cli,console,compact,mainframe")
    ap.add_argument("--size", default="1440x900")
    ap.add_argument("--scale", type=float, default=1)
    ap.add_argument("--thread", help="public thread id (default: the QA fixture)")
    ap.add_argument("--agents", action="store_true", help="open the agents pane")
    ap.add_argument("--agent", action="store_true", help="open the first subagent")
    ap.add_argument("--pane-width", type=int, help="agents pane width in px (256-896)")
    ap.add_argument("--live", action="store_true", help="show live docks (browser-only JSON rewrite)")
    ap.add_argument("--theme", help="theme id, or custom:<ink>,<brass> e.g. custom:#121212,#f2cf4a")
    ap.add_argument("--backdrop", help="backdrop id, optionally with strength 12-55, e.g. garden:40")
    ap.add_argument("--wait", type=float, default=0, help="extra seconds before the screenshot (let a backdrop grow)")
    ap.add_argument("--out", default="/tmp/fuse-qa-shots")
    asyncio.run(main(ap.parse_args()))
