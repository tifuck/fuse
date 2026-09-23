#!/usr/bin/env python3
"""Live end-to-end token-accounting audit against Fuse /v1.

Sends a long-prefix multi-turn chat (streamed, plus one non-stream control)
through the OpenAI-compatible API for Grok and a local OpenAI-compat backend
(llama.cpp). Optionally ChatGPT. Independently tokenizes the prompt and the
visible completion, parses the provider usage frame, then compares both to
the usage_events / api_keys / provider_usage / credentials rows Fuse stored.

Plan-percentage meters (SuperGrok / ChatGPT / Z.AI) are provider billing
snapshots, not a function of Fuse token totals. This script snapshots those
percentages and the Fuse-side per-credential token counters; it does not
expect the billed % to move in lockstep with a handful of test requests.

Usage:
  /root/fuse/venv/bin/python tests/audit_tokens_e2e.py
  /root/fuse/venv/bin/python tests/audit_tokens_e2e.py --skip-chatgpt --skip-grok
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import accounts, db, stats  # noqa: e402
from app.proxy import usage_from_obj  # noqa: e402

BASE = "https://127.0.0.1:8088"
AUDIT_USER_ID = 4
USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "output_tokens",
    "visible_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "total_tokens",
)


def _enc():
    try:
        import tiktoken
    except ImportError:
        return None
    for name in ("o200k_base", "cl100k_base"):
        try:
            return tiktoken.get_encoding(name)
        except Exception:
            continue
    return None


ENC = None


def _text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return str(content)


def count_text(text: str) -> int:
    if ENC is not None:
        return len(ENC.encode(text or ""))
    # Fallback: ~4 chars/token, used only if tiktoken is missing.
    return max(1, (len(text or "") + 3) // 4) if text else 0


def count_messages(messages: list[dict]) -> dict:
    """Independent local estimate. Chat-format overhead is approximate."""
    content = 0
    for msg in messages:
        content += count_text(_text(msg.get("content")))
    # OpenAI-style chat template: per-message role framing + reply priming.
    chat = content + 3 + 4 * len(messages)
    return {"content_tokens": content, "chat_tokens": chat}


def parse_sse(raw: bytes) -> tuple[str, dict, dict]:
    text_out = []
    last_usage = {}
    raw_usage = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        for choice in obj.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            msg = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            piece = delta.get("content") or msg.get("content") or ""
            if isinstance(piece, str) and piece:
                text_out.append(piece)
        got = usage_from_obj(obj)
        if any(got.get(k) for k in USAGE_KEYS):
            last_usage = got
            raw_usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
    return "".join(text_out), last_usage, raw_usage


def rowd(row) -> dict:
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


def snapshot(user_id: int, key_id: int) -> dict:
    events = db.fetchall(
        "SELECT * FROM usage_events WHERE api_key_id=? ORDER BY id", (key_id,)
    )
    return {
        "max_event_id": int(db.fetchone("SELECT COALESCE(MAX(id),0) AS n FROM usage_events")["n"]),
        "key": rowd(db.fetchone("SELECT * FROM api_keys WHERE id=?", (key_id,))),
        "providers": {
            r["provider"]: rowd(r)
            for r in db.fetchall("SELECT * FROM provider_usage WHERE user_id=?", (user_id,))
        },
        "creds": {
            r["id"]: rowd(r)
            for r in db.fetchall(
                """SELECT c.id, s.type, c.prompt_tokens, c.completion_tokens, c.output_tokens,
                          c.visible_tokens, c.reasoning_tokens, c.cached_tokens, c.cache_write_tokens,
                          c.total_tokens, c.requests, c.week_tokens, c.week_requests,
                          c.last_usage_percent, c.usage_percent_at, c.pool_held_until
                   FROM credentials c JOIN sources s ON s.id=c.source_id"""
            )
        },
        "events": [rowd(e) for e in events],
    }


def wait_event(key_id: int, after_id: int, timeout: float = 8.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = db.fetchone(
            "SELECT * FROM usage_events WHERE api_key_id=? AND id>? ORDER BY id DESC LIMIT 1",
            (key_id, after_id),
        )
        if last:
            return last
        time.sleep(0.15)
    return last


class Auditor:
    def __init__(self, key: str, key_id: int, user_id: int):
        self.key = key
        self.key_id = key_id
        self.user_id = user_id
        self.client = httpx.Client(base_url=BASE, verify=False, timeout=httpx.Timeout(180.0, connect=15.0))
        self.findings: list[dict] = []
        self.turns: list[dict] = []

    def close(self):
        self.client.close()

    def note(self, severity: str, title: str, **detail):
        item = {"severity": severity, "title": title, **detail}
        self.findings.append(item)
        mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[severity]
        extra = ""
        if detail:
            extra = " " + json.dumps({k: v for k, v in detail.items() if k != "title"}, default=str)[:400]
        print(f"  [{mark}] {title}{extra}")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def models(self) -> list[str]:
        r = self.client.get("/v1/models", headers=self._headers())
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data") or [] if m.get("id")]

    def chat(self, model: str, messages: list[dict], *, stream: bool, max_tokens: int, cache_key: str) -> dict:
        local_in = count_messages(messages)
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
            "temperature": 0,
            "prompt_cache_key": cache_key,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        before = int(db.fetchone("SELECT COALESCE(MAX(id),0) AS n FROM usage_events")["n"])
        t0 = time.time()
        r = self.client.post("/v1/chat/completions", headers=self._headers(), json=body)
        elapsed = time.time() - t0
        visible = ""
        parsed = {}
        raw_usage = {}
        obj = None
        if r.status_code != 200:
            try:
                err = r.json()
            except json.JSONDecodeError:
                err = {"error": r.text[:400]}
            self.note("fail", f"{model} HTTP {r.status_code}", error=err, stream=stream)
            return {
                "ok": False,
                "model": model,
                "stream": stream,
                "status": r.status_code,
                "error": err,
                "elapsed": elapsed,
                "local_in": local_in,
            }
        if stream:
            visible, parsed, raw_usage = parse_sse(r.content)
        else:
            obj = r.json()
            parsed = usage_from_obj(obj)
            raw_usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
            msg = ((obj.get("choices") or [{}])[0].get("message") or {})
            visible = _text(msg.get("content"))
        local_out = count_text(visible)
        event = wait_event(self.key_id, before)
        event_d = rowd(event)
        return {
            "ok": True,
            "model": model,
            "stream": stream,
            "status": r.status_code,
            "elapsed": round(elapsed, 3),
            "local_in": local_in,
            "local_out": local_out,
            "visible_text": visible,
            "provider": parsed,
            "raw_usage": raw_usage,
            "event": event_d,
            "event_id": event_d.get("id"),
        }

    def compare_turn(self, label: str, result: dict, *, expect_cache: bool = False) -> None:
        print(f"\n--- {label} ---")
        if not result.get("ok"):
            return
        prov = result["provider"]
        event = result["event"]
        local_in = result["local_in"]
        local_out = result["local_out"]
        print(
            f"  local in={local_in['content_tokens']} (chat~{local_in['chat_tokens']})  "
            f"local visible out={local_out}"
        )
        print(
            f"  provider in={prov.get('prompt_tokens')} out={prov.get('output_tokens')} "
            f"visible={prov.get('visible_tokens')} reasoning={prov.get('reasoning_tokens')} "
            f"cached={prov.get('cached_tokens')} total={prov.get('total_tokens')}"
        )
        if not event:
            self.note("fail", f"{label}: Fuse stored no usage_event")
            return
        print(
            f"  fuse     in={event.get('prompt_tokens')} out={event.get('output_tokens')} "
            f"visible={event.get('visible_tokens')} reasoning={event.get('reasoning_tokens')} "
            f"cached={event.get('cached_tokens')} total={event.get('total_tokens')} "
            f"status={event.get('usage_status')}/{event.get('request_status')}"
        )
        mismatches = []
        for k in USAGE_KEYS:
            pv, ev = int(prov.get(k) or 0), int(event.get(k) or 0)
            if pv != ev:
                mismatches.append(f"{k}: provider {pv} != fuse {ev}")
        if mismatches:
            self.note("fail", f"{label}: provider usage != Fuse event", mismatches=mismatches)
        else:
            self.note("ok", f"{label}: Fuse event matches provider usage frame")

        pt = int(prov.get("prompt_tokens") or 0)
        ot = int(prov.get("output_tokens") or 0)
        vis = int(prov.get("visible_tokens") or 0)
        rt = int(prov.get("reasoning_tokens") or 0)
        cached = int(prov.get("cached_tokens") or 0)
        total = int(prov.get("total_tokens") or 0)
        if vis + rt > ot and ot:
            self.note("fail", f"{label}: visible+reasoning exceeds output", visible=vis, reasoning=rt, output=ot)
        if cached > pt:
            self.note("fail", f"{label}: cached_tokens > prompt_tokens", cached=cached, prompt=pt)
        if total and pt + ot != total:
            # Grok sometimes has total as the authority; flag only a real split.
            if abs((pt + ot) - total) > 0:
                self.note("warn", f"{label}: total != prompt+output", prompt=pt, output=ot, total=total)

        chat = local_in["chat_tokens"]
        if pt:
            rel = abs(chat - pt) / max(pt, 1)
            if rel <= 0.25 or abs(chat - pt) <= 32:
                self.note("ok", f"{label}: local prompt estimate within 25% of provider", local=chat, provider=pt)
            else:
                self.note(
                    "warn",
                    f"{label}: local prompt estimate diverges (tokenizer/chat-template, not a Fuse bug)",
                    local=chat,
                    provider=pt,
                    rel=round(rel, 3),
                )
        if vis and local_out:
            rel = abs(local_out - vis) / max(vis, 1)
            if rel <= 0.35 or abs(local_out - vis) <= 8:
                self.note("ok", f"{label}: local visible-out estimate close to provider visible", local=local_out, provider=vis)
            elif rt and local_out <= vis + 2:
                self.note("ok", f"{label}: local visible-out matches visible (reasoning not in text)", local=local_out, provider=vis)
            else:
                self.note(
                    "warn",
                    f"{label}: local visible-out diverges (hidden reasoning / tokenizer)",
                    local=local_out,
                    provider_visible=vis,
                    provider_output=ot,
                    reasoning=rt,
                )
        if expect_cache:
            if cached > 0:
                self.note("ok", f"{label}: prompt cache hit", cached=cached, prompt=pt, share=round(100 * cached / max(pt, 1), 1))
            else:
                self.note("warn", f"{label}: expected cached_tokens > 0 on follow-up (provider cache is best-effort)", prompt=pt)

        result["compared"] = True
        self.turns.append(result)

    def check_aggregates(self, before: dict, after: dict) -> None:
        print("\n=== aggregate deltas (this audit key) ===")
        new_events = [
            e
            for e in db.fetchall(
                "SELECT * FROM usage_events WHERE api_key_id=? AND id>?",
                (self.key_id, before["max_event_id"]),
            )
        ]
        sums = {k: 0 for k in USAGE_KEYS}
        sums["requests"] = 0
        by_provider: dict[str, dict] = {}
        by_cred: dict[int, dict] = {}
        for e in new_events:
            sums["requests"] += 1
            for k in USAGE_KEYS:
                sums[k] += int(e[k] or 0)
            p = e["source_type"] or "unknown"
            bucket = by_provider.setdefault(p, {k: 0 for k in (*USAGE_KEYS, "requests")})
            bucket["requests"] += 1
            for k in USAGE_KEYS:
                bucket[k] += int(e[k] or 0)
            cid = e["credential_id"]
            if cid:
                cb = by_cred.setdefault(int(cid), {k: 0 for k in (*USAGE_KEYS, "requests")})
                cb["requests"] += 1
                for k in USAGE_KEYS:
                    cb[k] += int(e[k] or 0)

        key_before, key_after = before["key"], after["key"]
        for k in (*USAGE_KEYS, "requests"):
            delta = int(key_after.get(k) or 0) - int(key_before.get(k) or 0)
            expect = sums.get(k, 0)
            if delta == expect:
                self.note("ok", f"api_keys.{k} += event sum", delta=delta)
            else:
                self.note("fail", f"api_keys.{k} delta != event sum", delta=delta, events=expect)

        for provider, expect in by_provider.items():
            pb = before["providers"].get(provider) or {}
            pa = after["providers"].get(provider) or {}
            for k in (*USAGE_KEYS, "requests"):
                delta = int(pa.get(k) or 0) - int(pb.get(k) or 0)
                if delta == expect[k]:
                    self.note("ok", f"provider_usage[{provider}].{k} += event sum", delta=delta)
                else:
                    self.note(
                        "fail",
                        f"provider_usage[{provider}].{k} delta != event sum",
                        delta=delta,
                        events=expect[k],
                    )

        for cid, expect in by_cred.items():
            cb = before["creds"].get(cid) or {}
            ca = after["creds"].get(cid) or {}
            for k in (*USAGE_KEYS, "requests"):
                delta = int(ca.get(k) or 0) - int(cb.get(k) or 0)
                if delta == expect[k]:
                    self.note("ok", f"credentials[{cid}].{k} += event sum", delta=delta)
                else:
                    self.note(
                        "fail",
                        f"credentials[{cid}].{k} delta != event sum",
                        delta=delta,
                        events=expect[k],
                    )

        view = stats.current_usage(self.user_id)
        mix = view["mix"]
        mix_sum = sum(m["tokens"] for m in mix) or 0
        share_total = sum(m["share"] for m in mix)
        print("\n=== dashboard mix (lifetime, this user) ===")
        for m in mix:
            print(f"  {m['name']}: {m['tokens']:,}  {m['share']}%")
        if mix_sum and abs(share_total - 100) <= 1:
            self.note("ok", "mix percentages sum to ~100", share_total=share_total, mix_sum=mix_sum)
        elif mix_sum:
            self.note("fail", "mix percentages do not sum to 100", share_total=share_total)

        # Mix categories must not double-count: uncached + cached + visible + reasoning
        # should equal (or be very close to) total tokens.
        totals = view["totals"]
        reconstructed = (
            totals["uncached_tokens"]
            + totals["cached_tokens"]
            + totals.get("cache_write_tokens", 0)
            + totals["visible_tokens"]
            + totals["reasoning_tokens"]
        )
        if abs(reconstructed - totals["total_tokens"]) <= 1 or reconstructed <= totals["total_tokens"]:
            self.note(
                "ok",
                "mix parts do not exceed lifetime total",
                reconstructed=reconstructed,
                total=totals["total_tokens"],
            )
        else:
            self.note(
                "fail",
                "mix parts exceed lifetime total (double-count)",
                reconstructed=reconstructed,
                total=totals["total_tokens"],
            )

        print("\n=== per-plan Fuse token counters vs billing % (not computed from tokens) ===")
        for cid, ca in after["creds"].items():
            if ca.get("type") not in ("grok", "chatgpt", "zai"):
                continue
            cb = before["creds"].get(cid) or {}
            tok_delta = int(ca.get("total_tokens") or 0) - int(cb.get("total_tokens") or 0)
            if tok_delta == 0 and int(ca.get("requests") or 0) == int(cb.get("requests") or 0):
                continue
            print(
                f"  cred {cid} ({ca.get('type')}): Fuse tokens {cb.get('total_tokens')} -> {ca.get('total_tokens')} "
                f"(+{tok_delta}); billed % {cb.get('last_usage_percent')} -> {ca.get('last_usage_percent')}"
            )


def long_prefix(tag: str) -> str:
    pad = (
        "Reusable audit prefix for measuring prompt-cache hits across turns. "
        "Keep this text identical on every follow-up so the provider can hash "
        "the leading tokens and serve them from cache. Describe a quiet workshop: "
        "oak benches, brass calipers, a fuse strip glowing along the wall, "
        "numbered drawers of wire gauges, and a ledger of token counts. "
    ) * 28
    return f"TOKEN-AUDIT {tag}\n{pad}"


def run_model(aud: Auditor, model: str, *, turns: int, max_tokens: int, tag: str) -> None:
    prefix = long_prefix(tag)
    cache_key = f"audit-{tag}"
    history: list[dict] = [
        {"role": "system", "content": prefix},
    ]
    print(f"\n==== {model}  turns={turns}  max_tokens={max_tokens} ====")
    print(f"  system prefix local tokens: {count_text(prefix)}")

    # Turn 1 streamed.
    history.append({"role": "user", "content": "Reply with exactly this line: AUDIT-TURN-1-PONG"})
    r1 = aud.chat(model, history, stream=True, max_tokens=max_tokens, cache_key=cache_key)
    aud.compare_turn(f"{model} stream turn 1", r1, expect_cache=False)
    if not r1.get("ok"):
        return
    history.append({"role": "assistant", "content": r1["visible_text"] or "AUDIT-TURN-1-PONG"})

    for n in range(2, turns + 1):
        time.sleep(1.2)
        history.append({"role": "user", "content": f"Reply with exactly this line: AUDIT-TURN-{n}-PONG"})
        rn = aud.chat(model, history, stream=True, max_tokens=max_tokens, cache_key=cache_key)
        aud.compare_turn(f"{model} stream turn {n}", rn, expect_cache=True)
        if not rn.get("ok"):
            return
        history.append({"role": "assistant", "content": rn["visible_text"] or f"AUDIT-TURN-{n}-PONG"})

    # One non-stream control on a fresh short prompt so we compare the JSON path.
    time.sleep(0.4)
    control = [
        {"role": "system", "content": "You are a terse assistant."},
        {"role": "user", "content": "Reply with the single word pong."},
    ]
    rs = aud.chat(model, control, stream=False, max_tokens=16, cache_key=f"{cache_key}-nostream")
    aud.compare_turn(f"{model} non-stream control", rs, expect_cache=False)


def pick_llama(names: list[str]) -> str | None:
    skip = ("grok", "gpt-", "glm-", "openrouter/", "minimax", "kimi", "qwen3-coder", "deepseek")
    for n in names:
        low = n.lower()
        if any(low.startswith(s) or s in low for s in skip) and "huihui" not in low and "abliterated" not in low:
            continue
        if "huihui" in low or "abliterated" in low or "qwen" in low:
            return n
    for n in names:
        low = n.lower()
        if not low.startswith(("grok", "gpt-", "glm-", "openrouter/")):
            return n
    return None


def historical_scan() -> list[dict]:
    issues = []
    rows = db.fetchall(
        """SELECT id, source_type, prompt_tokens, completion_tokens, output_tokens, visible_tokens,
                  reasoning_tokens, cached_tokens, cache_write_tokens, total_tokens, request_status, usage_status
           FROM usage_events"""
    )
    for r in rows:
        notes = []
        p, c, o, v, rt = r["prompt_tokens"], r["completion_tokens"], r["output_tokens"], r["visible_tokens"], r["reasoning_tokens"]
        ch, cw, t = r["cached_tokens"], r["cache_write_tokens"], r["total_tokens"]
        if ch > p:
            notes.append("cached>prompt")
        if cw > max(p - ch, 0):
            notes.append("cache_write overflow")
        if v > o:
            notes.append("visible>output")
        if rt > o:
            notes.append("reasoning>output")
        if t and t != p + o:
            notes.append(f"total {t} != prompt+output {p + o}")
        if r["source_type"] == "grok" and c > 0 and v == 0 and rt > 0:
            notes.append("grok visible=0 while completion>0 (streaming tap bug)")
        if notes:
            issues.append({"id": r["id"], "type": r["source_type"], "notes": notes})
    # Lifetime rollups vs events.
    ev = {
        row["source_type"]: row
        for row in db.fetchall(
            """SELECT source_type, SUM(prompt_tokens) p, SUM(output_tokens) o, SUM(visible_tokens) v,
                      SUM(total_tokens) t, COUNT(*) n FROM usage_events GROUP BY source_type"""
        )
    }
    for row in db.fetchall(
        "SELECT provider, SUM(prompt_tokens) p, SUM(output_tokens) o, SUM(visible_tokens) v, SUM(total_tokens) t, SUM(requests) n FROM provider_usage GROUP BY provider"
    ):
        e = ev.get(row["provider"])
        if not e:
            continue
        if int(e["p"] or 0) != int(row["p"] or 0) or int(e["t"] or 0) != int(row["t"] or 0) or int(e["n"] or 0) != int(row["n"] or 0):
            issues.append(
                {
                    "id": None,
                    "type": row["provider"],
                    "notes": [
                        f"provider_usage drift vs events: events p/t/n={e['p']}/{e['t']}/{e['n']} "
                        f"rollup {row['p']}/{row['t']}/{row['n']}"
                    ],
                }
            )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-grok", action="store_true")
    parser.add_argument("--skip-llama", action="store_true")
    parser.add_argument("--skip-chatgpt", action="store_true")
    parser.add_argument("--skip-zai", action="store_true")
    parser.add_argument("--turns", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=48)
    parser.add_argument("--keep-key", action="store_true")
    args = parser.parse_args()

    global ENC
    ENC = _enc()
    db.connect()

    print("Fuse token-accounting audit")
    print(f"  tokenizer: {'tiktoken ' + ENC.name if ENC is not None else 'char/4 fallback'}")
    print(f"  time: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")

    hist = historical_scan()
    print(f"\n=== historical DB scan: {len(hist)} issue(s) ===")
    for item in hist:
        print(f"  event {item['id']} {item['type']}: {', '.join(item['notes'])}")
        # Pre-existing, not counted against this run unless we reintroduce them.

    user = accounts.get_user(AUDIT_USER_ID)
    if not user:
        print("audit user id 4 missing", file=sys.stderr)
        return 2
    raw = accounts.create_api_key(AUDIT_USER_ID, f"token-audit-{int(time.time())}")
    key_row = db.fetchone("SELECT id FROM api_keys WHERE key_hash=?", (accounts.key_hash(raw),))
    key_id = int(key_row["id"])
    aud = Auditor(raw, key_id, AUDIT_USER_ID)
    rc = 0
    try:
        names = aud.models()
        print(f"  catalog ({len(names)}): {', '.join(names[:24])}{'…' if len(names) > 24 else ''}")
        before = snapshot(AUDIT_USER_ID, key_id)

        if not args.skip_llama:
            llama = pick_llama(names)
            if not llama:
                aud.note("warn", "no llama/OpenAI-compat model in catalog", names=names[:20])
            else:
                run_model(aud, llama, turns=args.turns, max_tokens=args.max_tokens, tag=f"llama-{int(time.time())}")

        if not args.skip_grok:
            grok = "grok-4.5" if "grok-4.5" in names else ("grok-4.6" if "grok-4.6" in names else None)
            if not grok:
                aud.note("warn", "no grok model in catalog")
            else:
                run_model(aud, grok, turns=args.turns, max_tokens=args.max_tokens, tag=f"grok-{int(time.time())}")

        if not args.skip_chatgpt:
            gpt = "gpt-5.6-luna" if "gpt-5.6-luna" in names else None
            if not gpt:
                aud.note("warn", "no ChatGPT model in catalog (user may lack a donated OpenAI plan)")
            else:
                run_model(aud, gpt, turns=args.turns, max_tokens=min(args.max_tokens, 32), tag=f"gpt-{int(time.time())}")

        if not args.skip_zai:
            zai = "glm-5.3-flash" if "glm-5.3-flash" in names else None
            if not zai:
                aud.note("warn", "no glm-5.3-flash in catalog (user may lack pool access)")
            else:
                run_model(aud, zai, turns=args.turns, max_tokens=args.max_tokens, tag=f"zai-{int(time.time())}")

        after = snapshot(AUDIT_USER_ID, key_id)
        aud.check_aggregates(before, after)
    finally:
        aud.close()
        if not args.keep_key:
            accounts.delete_api_key(AUDIT_USER_ID, key_id)
            print(f"\n  deleted audit key id={key_id}")
        else:
            print(f"\n  kept audit key id={key_id} prefix={raw[:9]}")

    fails = [f for f in aud.findings if f["severity"] == "fail"]
    warns = [f for f in aud.findings if f["severity"] == "warn"]
    oks = [f for f in aud.findings if f["severity"] == "ok"]
    print(f"\n=== summary: {len(oks)} ok, {len(warns)} warn, {len(fails)} fail ===")
    if fails:
        rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
