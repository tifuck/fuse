"""OpenCode catalog, config merge, and curl|sh installer."""
from __future__ import annotations

import json

from .sources import chatgpt as chatgpt_mod
from .sources import devin as devin_mod
from .sources import models_by_source
from .sources import openrouter as openrouter_mod
from .sources import kimi as kimi_mod
from .sources import mimo as mimo_mod
from .sources import claude as claude_mod
from .sources import zai as zai_mod
from .sources.grok import grok_access_state
from .web.models import _model_display_name, _opencode_model_config

OPENCODE_SCHEMA = "https://opencode.ai/config.json"
FUSE_NPM = "@ai-sdk/openai-compatible"
_OPENCODE_NAME_PREFIX = {
    "devin": "Devin",
}


def opencode_display_name(type_id: str, name: str) -> str:
    """Prefix reseller catalogs so OpenCode can tell GPT-on-Devin from GPT-on-ChatGPT."""
    label = (name or "").strip()
    prefix = _OPENCODE_NAME_PREFIX.get(type_id or "")
    if not prefix or not label:
        return label
    if label.lower().startswith(prefix.lower() + " "):
        return label
    return f"{prefix} {label}"


async def model_options(user_id: int, groups: list[dict] | None = None) -> list[dict]:
    if groups is None:
        grok = grok_access_state(user_id)
        chatgpt = chatgpt_mod.chatgpt_access_state(user_id)
        zai = zai_mod.zai_access_state(user_id)
        devin = devin_mod.devin_access_state(user_id)
        kimi = kimi_mod.kimi_access_state(user_id)
        mimo = mimo_mod.mimo_access_state(user_id)
        claude = claude_mod.claude_access_state(user_id)
        groups = await models_by_source(
            allow_grok=grok["ok"],
            allow_chatgpt=chatgpt["ok"],
            allow_zai=zai["ok"],
            allow_devin=devin["ok"],
            allow_kimi=kimi["ok"],
            allow_mimo=mimo["ok"],
            allow_claude=claude["ok"],
            user_id=user_id,
        )
    entries: list[dict] = []
    seen: set[str] = set()
    openrouter_catalog = None
    for group in groups:
        if not group.get("models"):
            continue
        if group["type"] == "openrouter" and openrouter_catalog is None:
            openrouter_catalog = openrouter_mod.cached_catalog()
            if openrouter_catalog is None:
                openrouter_catalog = await openrouter_mod.fetch_catalog()
        for model in group["models"]:
            mid = model
            if group["type"] == "devin":
                mid = devin_mod.canonical_fuse_id(model) or model
                if not mid.startswith("devin/"):
                    mid = f"devin/{mid}"
            if mid in seen:
                continue
            seen.add(mid)
            display = opencode_display_name(
                group["type"],
                _model_display_name(mid, group["type"], openrouter_catalog),
            )
            entries.append(
                {
                    "id": mid,
                    "name": display,
                    "type": group["type"],
                    "config": _opencode_model_config(display, group["type"], mid),
                }
            )
    return entries


def existing_fuse_api_key(config: dict | None) -> str:
    """Return provider.fuse.options.apiKey when a Fuse key is already saved."""
    if not isinstance(config, dict):
        return ""
    provider = config.get("provider")
    if not isinstance(provider, dict):
        return ""
    fuse = provider.get("fuse")
    if not isinstance(fuse, dict):
        return ""
    options = fuse.get("options")
    if not isinstance(options, dict):
        return ""
    for key_name in ("apiKey", "api_key"):
        value = options.get(key_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def fuse_provider(entries: list[dict], *, base_url: str = "", api_key: str = "") -> dict:
    models = {item["id"]: item["config"] for item in entries}
    options: dict[str, str] = {}
    if base_url:
        options["baseURL"] = base_url
    if api_key:
        options["apiKey"] = api_key
    provider = {
        "npm": FUSE_NPM,
        "name": "Fuse",
        "models": models,
    }
    if options:
        provider["options"] = options
    return provider


def merge_opencode_config(
    existing: dict | None,
    *,
    models: dict,
    base_url: str = "",
    api_key: str = "",
) -> dict:
    """Replace provider.fuse.models; keep every other OpenCode setting."""
    out = dict(existing) if isinstance(existing, dict) else {}
    out.setdefault("$schema", OPENCODE_SCHEMA)
    provider = dict(out.get("provider") or {}) if isinstance(out.get("provider"), dict) else {}
    fuse = dict(provider.get("fuse") or {}) if isinstance(provider.get("fuse"), dict) else {}
    options = dict(fuse.get("options") or {}) if isinstance(fuse.get("options"), dict) else {}
    if base_url:
        options["baseURL"] = base_url.rstrip("/")
    if api_key:
        options["apiKey"] = api_key
    fuse["npm"] = FUSE_NPM
    fuse["name"] = "Fuse"
    if options:
        fuse["options"] = options
    fuse["models"] = dict(models)
    provider["fuse"] = fuse
    out["provider"] = provider
    enabled = out.get("enabled_providers")
    if isinstance(enabled, list) and "fuse" not in enabled:
        out["enabled_providers"] = [*enabled, "fuse"]
    disabled = out.get("disabled_providers")
    if isinstance(disabled, list) and "fuse" in disabled:
        out["disabled_providers"] = [item for item in disabled if item != "fuse"]
    return out


def strip_jsonc(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text or "")
    in_str = False
    esc = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_opencode_text(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}
    for candidate in (raw, strip_jsonc(raw)):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("OpenCode config is not valid JSON")


def install_script(*, origin: str, api_base: str) -> str:
    origin = (origin or "").rstrip("/")
    api_base = (api_base or "").rstrip("/")
    return _INSTALL_SH.replace("@@ORIGIN@@", origin).replace("@@API@@", api_base)


_INSTALL_SH = r'''#!/bin/sh
# Fuse OpenCode installer. Updates only provider.fuse in your OpenCode config.
set -eu
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to install Fuse for OpenCode." >&2
  exit 1
fi
export FUSE_ORIGIN="@@ORIGIN@@"
export FUSE_API="@@API@@"
exec python3 - "$@" <<'PY'
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

ORIGIN = os.environ.get("FUSE_ORIGIN", "").rstrip("/")
API = os.environ.get("FUSE_API", "").rstrip("/")
SCHEMA = "https://opencode.ai/config.json"
NPM = "@ai-sdk/openai-compatible"


def strip_jsonc(text: str) -> str:
    out = []
    i = 0
    n = len(text or "")
    in_str = False
    esc = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_config(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}
    for candidate in (raw, strip_jsonc(raw)):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise SystemExit("OpenCode config is not valid JSON or JSONC.")


def existing_key(config: dict) -> str:
    if not isinstance(config, dict):
        return ""
    provider = config.get("provider")
    if not isinstance(provider, dict):
        return ""
    fuse = provider.get("fuse")
    if not isinstance(fuse, dict):
        return ""
    options = fuse.get("options")
    if not isinstance(options, dict):
        return ""
    for name in ("apiKey", "api_key"):
        value = options.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def merge(existing: dict, models: dict, base_url: str, api_key: str) -> dict:
    out = dict(existing or {})
    out.setdefault("$schema", SCHEMA)
    provider = dict(out.get("provider") or {}) if isinstance(out.get("provider"), dict) else {}
    fuse = dict(provider.get("fuse") or {}) if isinstance(provider.get("fuse"), dict) else {}
    options = dict(fuse.get("options") or {}) if isinstance(fuse.get("options"), dict) else {}
    if base_url:
        options["baseURL"] = base_url.rstrip("/")
    if api_key:
        options["apiKey"] = api_key
    fuse["npm"] = NPM
    fuse["name"] = "Fuse"
    fuse["options"] = options
    fuse["models"] = dict(models)
    provider["fuse"] = fuse
    out["provider"] = provider
    enabled = out.get("enabled_providers")
    if isinstance(enabled, list) and "fuse" not in enabled:
        out["enabled_providers"] = [*enabled, "fuse"]
    disabled = out.get("disabled_providers")
    if isinstance(disabled, list) and "fuse" in disabled:
        out["disabled_providers"] = [item for item in disabled if item != "fuse"]
    return out


def tty():
    try:
        return open("/dev/tty", "r+", encoding="utf-8", errors="replace")
    except OSError:
        return None


def ask(prompt: str, *, secret: bool = False, default: str = "") -> str:
    stream = tty()
    if stream is None:
        return default
    try:
        stream.write(prompt)
        stream.flush()
        if secret:
            try:
                import termios, tty as tty_mod
                fd = stream.fileno()
                old = termios.tcgetattr(fd)
                try:
                    tty_mod.setraw(fd)
                    buf = []
                    while True:
                        ch = stream.read(1)
                        if not ch or ch in "\r\n":
                            stream.write("\n")
                            break
                        if ch == "\x03":
                            raise KeyboardInterrupt
                        if ch in ("\x7f", "\b"):
                            if buf:
                                buf.pop()
                            continue
                        buf.append(ch)
                    return "".join(buf)
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                return stream.readline().rstrip("\n")
        return stream.readline().rstrip("\n")
    finally:
        stream.close()


def say(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def config_path(explicit: str) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("OPENCODE_CONFIG") or ""
    if env:
        return Path(env).expanduser()
    base = Path.home() / ".config" / "opencode"
    for name in ("opencode.json", "opencode.jsonc"):
        candidate = base / name
        if candidate.exists():
            return candidate
    return base / "opencode.json"


def fetch_catalog(api: str, key: str) -> dict:
    req = urllib.request.Request(
        api.rstrip("/") + "/opencode",
        headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:240]
        raise SystemExit(f"Fuse catalog request failed (HTTP {exc.code}): {detail}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach Fuse at {api}: {exc.reason}") from None
    data = json.loads(body)
    fuse = ((data.get("provider") or {}).get("fuse") or {})
    models = fuse.get("models")
    if not isinstance(models, dict) or not models:
        raise SystemExit("Fuse returned no models for this API key.")
    return fuse


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Install or update Fuse models in OpenCode.")
    parser.add_argument("--api-key", default="", help="Overwrite the saved Fuse API key.")
    parser.add_argument("--base-url", default=API)
    parser.add_argument("--config", default="")
    parser.add_argument("-y", "--yes", action="store_true", help="Do not prompt for confirmation.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    say("Fuse OpenCode installer")
    say("This only changes the Fuse provider. Other OpenCode settings stay as they are.")
    path = config_path(args.config)
    existing = parse_config(path.read_text(encoding="utf-8")) if path.exists() else {}
    stored = existing_key(existing)
    explicit = (args.api_key or "").strip()
    env_key = (os.environ.get("FUSE_API_KEY") or "").strip()
    key = explicit or stored or env_key
    if stored and not explicit:
        say("Using the Fuse API key already saved in OpenCode config.")
    if not key:
        key = ask("Fuse API key (sk-…): ", secret=True).strip()
    if not key:
        say("No API key. Save one in OpenCode config, or re-run with --api-key sk-…")
        return 2
    fuse = fetch_catalog(args.base_url, key)
    models = fuse.get("models") or {}
    say(f"Fetched {len(models)} Fuse models.")
    say(f"Config: {path}")
    if path.exists():
        say("Existing Fuse models will be replaced. The saved API key is left as-is.")
    else:
        say("No OpenCode config yet; one will be created.")
    if not args.yes and not stored:
        reply = ask("Continue? [Y/n] ", default="y").strip().lower()
        if reply in ("n", "no"):
            say("Cancelled.")
            return 1
    write_key = explicit if explicit else ("" if stored else key)
    merged = merge(existing, models, args.base_url, write_key)
    rendered = json.dumps(merged, indent=2) + "\n"
    if args.dry_run:
        sys.stdout.write(rendered)
        return 0
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(path.name + f".bak-{stamp}")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(backup, stat.S_IRUSR | stat.S_IWUSR)
        say(f"Backup: {backup}")
    path.write_text(rendered, encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    say(f"Wrote {len(models)} models to {path}")
    say("Next: opencode models fuse")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        say("Cancelled.")
        raise SystemExit(130)
PY
'''
