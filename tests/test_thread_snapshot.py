"""Thread snapshot transport: lossless segment packing and static caching."""
import asyncio
import json
import subprocess
from pathlib import Path

from app import chats

ROOT = Path(__file__).resolve().parents[1]


def _message(content, reasoning):
    return {
        "id": "m1", "role": "assistant", "content": content, "reasoning": reasoning,
        "segments": [
            {"type": "reasoning", "text": reasoning[:3]},
            {"type": "tool", "id": "t1"},
            {"type": "reasoning", "text": reasoning[3:]},
            {"type": "content", "text": content},
        ],
    }


def test_pack_replaces_duplicated_segment_text_with_utf16_lengths():
    # Astral-plane characters are two UTF-16 code units in JavaScript.
    msg = _message("done 🙂 ok", "a🙂bcdef")
    packed = chats.pack_segment_text([msg])[0]
    assert packed["segments"] == [
        {"type": "reasoning", "n": 4},
        {"type": "tool", "id": "t1"},
        {"type": "reasoning", "n": 4},
        {"type": "content", "n": 10},
    ]
    assert msg["segments"][0]["text"] == "a🙂b", "the input must not be mutated"


def test_pack_leaves_inexact_segments_unchanged():
    msg = _message("final answer", "thinking")
    msg["segments"][-1]["text"] = "final"  # no longer concatenates to content
    assert chats.pack_segment_text([msg]) == [msg]
    plain = {"id": "u", "role": "user", "content": "hi"}
    assert chats.pack_segment_text([plain]) == [plain]


def test_client_unpack_restores_the_server_segments():
    msgs = [_message("done 🙂 ok", "a🙂bcdef"), _message("x", "y" * 5)]
    packed = {"segments_packed": True, "messages": chats.pack_segment_text(msgs)}
    script = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8');
const ctx = vm.createContext({});
for (const name of ['unpackSegments', 'unpackThread']) {
  const start = src.indexOf(`function ${name}(`);
  vm.runInContext(src.slice(start, src.indexOf('\n}\n', start) + 3), ctx);
}
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(ctx.unpackThread(data)));
"""
    out = subprocess.run(["node", "-e", script, str(ROOT / "static" / "chat" / "net.js")],
                         input=json.dumps(packed, ensure_ascii=False), capture_output=True, text=True, check=True)
    restored = json.loads(out.stdout)
    assert "segments_packed" not in restored
    assert restored["messages"] == msgs


def test_versioned_static_assets_are_immutable(monkeypatch):
    from app.config import settings
    from app import static_assets
    from app.main import VersionedStaticFiles

    files = VersionedStaticFiles(directory=str(ROOT / "static"))

    def headers(query, path="chat-boot.js"):
        scope = {"type": "http", "method": "GET", "path": "/" + path, "query_string": query, "headers": []}
        return asyncio.run(files.get_response(path, scope)).headers

    current = static_assets.version("chat-boot.js").encode()
    monkeypatch.setattr(settings, "static_immutable", True)
    assert headers(b"v=" + current)["cache-control"] == "public, max-age=31536000, immutable"
    # A script's ?v= is its content version: a stale one (a page rendered
    # before a deploy) gets today's bytes, which must not be cached as that URL.
    assert headers(b"v=3")["cache-control"] == "no-cache"
    # Unversioned internal modules must revalidate, or a heuristically cached
    # copy can mismatch a newer versioned entry script.
    assert headers(b"")["cache-control"] == "no-cache"
    assert headers(b"xv=" + current)["cache-control"] == "no-cache"
    # Other files keep the plain rule: any ?v= is immutable.
    assert headers(b"v=3", "icon.svg")["cache-control"] == "public, max-age=31536000, immutable"
    monkeypatch.setattr(settings, "static_immutable", False)
    assert headers(b"v=" + current)["cache-control"] == "no-cache"
