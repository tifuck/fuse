"""Restricted SKILL.md frontmatter parser. No PyYAML."""
from __future__ import annotations

import re

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_DOC = 256 * 1024
DESC_MAX = 1024
COMPAT_MAX = 500
HINT_MAX = 80
NAME_MAX = 64

_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}
_BOOL_KEYS = {
    "user-invocable",
    "user_invocable",
    "disable-model-invocation",
    "disable_model_invocation",
}
_BLOCK = re.compile(r"^([>|])([+-]?)(?:\d+)?$")


class SkillError(Exception):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def normalize_slug(raw: str) -> str:
    text = (raw or "").strip().lower().replace("_", "-")
    text = re.sub(r"[^a-z0-9-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:NAME_MAX]


def _unquote(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _as_bool(raw: str) -> bool | None:
    key = raw.strip().lower()
    if key in _TRUE:
        return True
    if key in _FALSE:
        return False
    return None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _fold(text: str) -> str:
    paragraphs = re.split(r"\n\s*\n", text.strip("\n"))
    return "\n\n".join(" ".join(p.split()) for p in paragraphs).strip()


def _consume_block(lines: list[str], start: int, base_indent: int) -> tuple[str, int]:
    i = start
    while i < len(lines) and not lines[i].strip():
        i += 1
    collected: list[str] = []
    block_indent = None
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            collected.append("")
            i += 1
            continue
        ind = _indent(line)
        if ind <= base_indent:
            break
        if block_indent is None:
            block_indent = ind
        collected.append(line[block_indent:])
        i += 1
    while collected and collected[-1] == "":
        collected.pop()
    return "\n".join(collected), i


_MAX_NEST = 4


def _skip_deeper(lines: list[str], start: int, base_indent: int) -> int:
    i = start
    while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > base_indent):
        i += 1
    return i


def _consume_nested(lines: list[str], start: int, base_indent: int, depth: int = 1) -> tuple[object, int]:
    i = start
    mapping: dict[str, str] = {}
    sequence: list[str] = []
    kind = None
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        ind = _indent(line)
        if ind <= base_indent:
            break
        body = line.strip()
        if body.startswith("- "):
            kind = "list"
            sequence.append(_unquote(body[2:]))
            i += 1
            continue
        if ":" in body:
            kind = "map"
            key, _, rest = body.partition(":")
            key = key.strip()
            rest = rest.strip()
            match = _BLOCK.match(rest)
            if match:
                raw, i = _consume_block(lines, i + 1, ind)
                mapping[key] = _fold(raw) if match.group(1) == ">" else raw
            elif not rest:
                # Deeper metadata is unused; stringifying it level by level
                # re-escapes it each time (exponential size), so it is skipped.
                if depth >= _MAX_NEST:
                    i = _skip_deeper(lines, i + 1, ind)
                    mapping[key] = ""
                    continue
                nested, i = _consume_nested(lines, i + 1, ind, depth + 1)
                mapping[key] = nested if isinstance(nested, str) else str(nested)
            else:
                mapping[key] = _unquote(rest)
                i += 1
            continue
        i += 1
    if kind == "list":
        return sequence, i
    return mapping, i


def _parse_yaml_subset(block: str) -> dict:
    if "\t" in block:
        # Tabs in folded description bodies are rare; treat as spaces.
        block = block.replace("\t", "  ")
    data: dict = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line.startswith(" ") or line.startswith("\t"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not key:
            i += 1
            continue
        match = _BLOCK.match(rest)
        if match:
            raw, i = _consume_block(lines, i + 1, 0)
            value = _fold(raw) if match.group(1) == ">" else raw
            if key in _BOOL_KEYS:
                flag = _as_bool(value)
                data[key] = value if flag is None else flag
            else:
                data[key] = value
            continue
        if not rest:
            nested, i = _consume_nested(lines, i + 1, 0)
            data[key] = nested
            continue
        if key in _BOOL_KEYS:
            flag = _as_bool(rest)
            data[key] = _unquote(rest) if flag is None else flag
        else:
            data[key] = _unquote(rest)
        i += 1
    return data


def parse_skill_md(text: str, *, slug: str | None = None) -> dict:
    """Parse SKILL.md into {name, description, body, flags...}. Body has no frontmatter."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_DOC:
        raise SkillError("invalid SKILL.md")
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    raw = raw.lstrip()
    if not raw.startswith("---"):
        raise SkillError("invalid SKILL.md")
    rest = raw[3:]
    if rest.startswith("\n"):
        rest = rest[1:]
    end_m = re.search(r"\n-{3,}[ \t]*(?:\n|$)", rest)
    if end_m is None:
        raise SkillError("invalid SKILL.md")
    fm = rest[: end_m.start()]
    body = rest[end_m.end() :]
    if body.startswith("\n"):
        body = body[1:]
    fields = _parse_yaml_subset(fm)
    name_raw = str(fields.get("name") or "").strip()
    name = normalize_slug(name_raw)
    ident = slug if slug and NAME_RE.fullmatch(slug) else name
    if slug and not NAME_RE.fullmatch(slug or ""):
        ident = normalize_slug(slug) or name
    if not ident or not NAME_RE.fullmatch(ident):
        raise SkillError("invalid SKILL.md")
    display = name if NAME_RE.fullmatch(name) else ident
    desc = str(fields.get("description") or "").strip()
    when = str(fields.get("when-to-use") or fields.get("when_to_use") or "").strip()
    if when and when not in desc:
        desc = f"{desc} {when}".strip() if desc else when
    if not desc:
        first = next((ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#")), "")
        desc = first or f"Skill {ident}."
    desc = desc[:DESC_MAX]
    meta = fields.get("metadata") if isinstance(fields.get("metadata"), dict) else {}
    hint_raw = fields.get("argument-hint") or fields.get("argument_hint") or meta.get("argument-hint") or ""
    if isinstance(hint_raw, list):
        hint_raw = " ".join(str(x) for x in hint_raw)
    hint = str(hint_raw)[:HINT_MAX]
    user_inv = fields.get("user-invocable", fields.get("user_invocable", True))
    if isinstance(user_inv, str):
        parsed = _as_bool(user_inv)
        user_inv = True if parsed is None else parsed
    disable = fields.get("disable-model-invocation", fields.get("disable_model_invocation", False))
    if isinstance(disable, str):
        parsed = _as_bool(disable)
        disable = False if parsed is None else parsed
    compat = str(fields.get("compatibility") or "")[:COMPAT_MAX]
    license_id = str(fields.get("license") or "")[:80]
    return {
        "name": ident,
        "display_name": display,
        "description": desc,
        "body": body,
        "argument_hint": hint.strip(),
        "user_invocable": bool(user_inv),
        "disable_model_invocation": bool(disable),
        "compatibility": compat,
        "license": license_id,
    }
