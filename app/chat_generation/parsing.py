"""Pure parsing of visible assistant text and tool-call payloads.

Text cleanup (leaked EOS / chat-template tokens, XML tool dumps),
Qwen/Hermes/GLM XML and JSON tool-call extraction, streamed
tool-call accumulation, and the reasoning/thinking replay shape.
No I/O, no state, no provider calls."""

from __future__ import annotations

import json
import re


_ARG_LIMIT_FILE = 400_000
_ARG_LIMIT_ARTIFACT = 400_000
_ARG_OVERFLOW = "[Fuse: tool arguments exceeded the 400000-character limit; call not executed]"


_SPECIAL_TOKENS = re.compile(
    r"<\|(?:eos|endoftext|end|eot_id|im_end|im_start|end_of_text)\|?>"
    r"|</s>",
    re.I,
)
# A parameter value may quote a closing tag (an edit to this file, echo
# '</function>'), so each element ends at the first closing tag that sits on a
# structural boundary, without crossing the next opener of its kind. Only when
# there is none does the first closing tag end it.
_TOOL_XML_BLOCK = re.compile(
    r"<tool_calls?\b[^>]*>"
    r"(?:(?:(?!<tool_calls?\b).)*?(?:</function>|</invoke>)\s*|(?:(?!<function\s*=|<invoke\b).)*?)"
    r"</tool_calls?>",
    re.I | re.S,
)
_TOOL_XML_OPEN = re.compile(r"<tool_calls?\b", re.I)
_FUNCTION_XML = re.compile(
    r"<function\s*=\s*([A-Za-z][\w.-]*)>((?:(?!<function\s*=).)*?</parameter>\s*(?=</function>)|.*?)</function>",
    re.I | re.S,
)
_INVOKE_XML = re.compile(
    r"<invoke\s+name\s*=\s*[\"']([^\"']+)[\"']>((?:(?!<invoke\b).)*?</parameter>\s*(?=</invoke>)|.*?)</invoke>",
    re.I | re.S,
)
_PARAM_END = r"(?=</parameter>\s*(?:<parameter\b|</function>|</invoke>|\Z))"
_PARAM_EQ_XML = re.compile(
    r"<parameter\s*=\s*([A-Za-z][\w.-]*)>((?:(?!<parameter\b).)*?" + _PARAM_END + r"|.*?)</parameter>",
    re.I | re.S,
)
_PARAM_NAME_XML = re.compile(
    r"<parameter\s+name\s*=\s*[\"']([^\"']+)[\"']>((?:(?!<parameter\b).)*?" + _PARAM_END + r"|.*?)</parameter>",
    re.I | re.S,
)


def strip_special_tokens(text: str) -> str:
    """Remove leaked EOS / chat-template tokens from visible model text."""
    if not text:
        return text
    return _SPECIAL_TOKENS.sub("", text)


def drop_tool_xml(text: str, start: int = 0) -> tuple[str, str]:
    """Split assistant text into (visible, incomplete <tool_call> tail).

    Complete XML tool dumps are dropped so they are not shown as the answer.
    An unclosed opener is held back until the next chunk or round end.
    Markdown code is quoted text that extract_text_tool_calls never runs, so a
    call shown there stays visible. Only text[start:] is rewritten; the settled
    prefix is read to tell whether a later opener sits inside code.
    """
    if not text:
        return "", ""
    if not _TOOL_XML_OPEN.search(text, start):
        return text, ""
    code = [match.span() for match in _MD_CODE.finditer(text)]

    def quoted(index: int) -> bool:
        return any(lo <= index < hi for lo, hi in code)

    parts, pos = [text[:start]], start
    for block in _TOOL_XML_BLOCK.finditer(text, start):
        if not quoted(block.start()):
            parts.append(text[pos:block.start()])
            pos = block.end()
    last = None
    for match in _TOOL_XML_OPEN.finditer(text, pos):
        if not quoted(match.start()):
            last = match
    if last:
        parts.append(text[pos:last.start()])
        return "".join(parts), text[last.start():]
    parts.append(text[pos:])
    return "".join(parts), ""


_TOOL_XML_NAME = re.compile(
    r"<function\s*=\s*([A-Za-z][\w.-]*)|<invoke\s+name\s*=\s*[\"']([^\"']+)|\"name\"\s*:\s*\"([^\"]+)\"",
    re.I,
)


def tool_xml_start(text: str, start: int = 0) -> int:
    """Offset of the first <tool_call> opener at or after start, or -1."""
    match = _TOOL_XML_OPEN.search(text or "", max(0, start))
    return match.start() if match else -1


def tool_xml_name(text: str, at: int) -> str:
    """Tool name written so far in the XML call opened at `at`, or ""."""
    match = _TOOL_XML_NAME.search((text or "")[at : at + 600])
    return next((group for group in match.groups() if group), "")[:64] if match else ""


def _xml_param_value(raw: str):
    text = (raw or "").strip()
    quoted = len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'"
    if quoted:
        text = text[1:-1]
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return text
    # A quoted scalar is a string: "123" must not become 123 (or true/null).
    return text if quoted and not isinstance(value, (dict, list)) else value


def _xml_text(raw: str) -> str:
    """A string parameter as written. Only the newline after the opening tag
    and the one before the closing tag are layout (as Qwen's parser reads
    them); a bare JSON string literal ("README.md") is unquoted."""
    text = raw or ""
    text = text[1:] if text.startswith("\n") else text
    text = text[:-1] if text.endswith("\n") else text
    if text.startswith('"'):
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, ValueError, RecursionError):
            return text
        return value if isinstance(value, str) else text
    return text


def _xml_params(body: str) -> dict:
    """Raw parameter text; _xml_call types it against the tool's schema."""
    args = {}
    for match in _PARAM_EQ_XML.finditer(body or ""):
        args[match.group(1)] = match.group(2)
    for match in _PARAM_NAME_XML.finditer(body or ""):
        args[match.group(1)] = match.group(2)
    return args


def _string_params(tool: str) -> set[str]:
    from ..chat_tools import _schema_for

    schema = _schema_for(tool) or {}
    props = ((schema.get("function") or {}).get("parameters") or {}).get("properties") or {}
    return {key for key, spec in props.items() if isinstance(spec, dict) and spec.get("type") == "string"}


def _xml_call(name: str, args: dict, index: int, *, coding: bool, raw: bool = False) -> dict | None:
    from ..chat_tools import suggest_tool

    resolved = suggest_tool(name, json.dumps(args, ensure_ascii=False), coding=coding) or (name or "").strip()
    if not resolved:
        return None
    if raw:
        # A string parameter is text even when it reads as JSON or a number
        # (write_file content '{"a": 1}', '42'); the rest are typed values.
        strings = _string_params(resolved)
        args = {key: _xml_text(value) if key in strings else _xml_param_value(value) for key, value in args.items()}
    return {
        "id": f"xml_{index}",
        "type": "function",
        "function": {
            "name": resolved[:64],
            "arguments": _bounded_arguments(json.dumps(args, ensure_ascii=False)),
        },
    }


def _calls_from_xml_body(body: str, *, coding: bool, start: int = 0) -> list[dict]:
    calls = []
    for match in _FUNCTION_XML.finditer(body or ""):
        rec = _xml_call(match.group(1), _xml_params(match.group(2)), start + len(calls), coding=coding, raw=True)
        if rec:
            calls.append(rec)
    if calls:
        return calls
    for match in _INVOKE_XML.finditer(body or ""):
        rec = _xml_call(match.group(1), _xml_params(match.group(2)), start + len(calls), coding=coding, raw=True)
        if rec:
            calls.append(rec)
    if calls:
        return calls
    blob = re.sub(
        r"^<tool_calls?\b[^>]*>|</tool_calls?>$",
        "",
        (body or "").strip(),
        flags=re.I,
    ).strip()
    if not blob:
        return calls
    try:
        parsed = json.loads(blob)
    except (json.JSONDecodeError, ValueError, RecursionError):
        parsed = None
    items = parsed if isinstance(parsed, list) else [parsed] if isinstance(parsed, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = item.get("name") or fn.get("name")
        args = (item.get("arguments") if "arguments" in item
                else item.get("parameters") or item.get("input") or fn.get("arguments") or {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError, RecursionError):
                args = {"_": args}
        if not isinstance(args, dict):
            args = {"value": args}
        rec = _xml_call(str(name or ""), args, start + len(calls), coding=coding)
        if rec:
            calls.append(rec)
    return calls


# Markdown code (fenced blocks, inline spans) is quoted text: a tool call shown
# as an example there must never execute.
# A fence closes only on a run of its own length (a ```` block may quote ```).
_MD_CODE = re.compile(r"(`{3,}|~{3,})[\s\S]*?(?:\1|$)|`[^`\n]+`")


def _md_split(content: str) -> list[str]:
    """Alternate plain text (even) and markdown code (odd), like re.split."""
    parts, pos = [], 0
    for match in _MD_CODE.finditer(content):
        parts += [content[pos:match.start()], match.group(0)]
        pos = match.end()
    parts.append(content[pos:])
    return parts


def extract_text_tool_calls(content: str, *, coding: bool = False) -> tuple[str, list[dict]]:
    """Parse Qwen/Hermes/GLM XML or JSON tool dumps out of assistant content."""
    if not content:
        return content, []
    lowered = content.lower()
    if "<tool" not in lowered and "<function" not in lowered and "<invoke" not in lowered:
        return content, []
    parts = _md_split(content)
    if len(parts) == 1:
        return _extract_plain_tool_calls(content, coding=coding)
    calls: list[dict] = []
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 0 and part:
            part, found = _extract_plain_tool_calls(part, coding=coding, start=len(calls), strip=False)
            calls.extend(found)
        out.append(part)
    return "".join(out).strip(), calls


def _extract_plain_tool_calls(content: str, *, coding: bool, start: int = 0, strip: bool = True) -> tuple[str, list[dict]]:
    if not content:
        return content, []
    lowered = content.lower()
    if "<tool" not in lowered and "<function" not in lowered and "<invoke" not in lowered:
        return content, []
    calls: list[dict] = []
    cleaned = content

    def _replace(match: re.Match) -> str:
        extracted = _calls_from_xml_body(match.group(0), coding=coding, start=start + len(calls))
        if not extracted:
            # Inner body without the outer tags, in case the regex included them.
            inner = match.group(0)
            inner = re.sub(r"^</?tool_calls?\b[^>]*>|</tool_calls?>$", "", inner, flags=re.I | re.S)
            extracted = _calls_from_xml_body(inner, coding=coding, start=start + len(calls))
        if extracted:
            calls.extend(extracted)
            return ""
        return match.group(0)

    cleaned = _TOOL_XML_BLOCK.sub(_replace, cleaned)
    if not calls:
        opener = _TOOL_XML_OPEN.search(cleaned)
        if opener:
            extracted = _calls_from_xml_body(cleaned[opener.end() :], coding=coding, start=start)
            if extracted:
                calls.extend(extracted)
                cleaned = cleaned[: opener.start()]
    if not calls:
        extracted = _calls_from_xml_body(cleaned, coding=coding, start=start)
        if extracted:
            calls.extend(extracted)
            cleaned = _FUNCTION_XML.sub("", cleaned)
            cleaned = _INVOKE_XML.sub("", cleaned)
    return (cleaned.strip() if strip else cleaned), calls


def _finalize_assistant_payload(content, reasoning, calls, finish, *, coding: bool = False):
    content = strip_special_tokens(content or "")
    reasoning = strip_special_tokens(reasoning or "")
    allc = [c for c in (calls or []) if isinstance(c, dict)]
    named = [c for c in allc if (c.get("function") or {}).get("name")]
    # Nameless entries ride along so the round loop can log them as
    # malformed calls; execution filters them again before tools run.
    native = allc
    content, extracted = extract_text_tool_calls(content, coding=coding)
    if not named and extracted:
        # Keep the nameless entries too: the round loop logs them as
        # malformed calls; the extracted XML calls carry the real work.
        native = [*allc, *extracted]
    if named or extracted:
        finish = "tool_calls"
    return content, reasoning, native, finish


def calls_incomplete(calls: list) -> bool:
    """True when any tool call has truncated JSON arguments.

    Empty arguments are a complete no-argument call (some OpenAI-compatible
    providers send "" instead of "{}"); the tool runner executes them as {}.
    The oversize marker is not a truncation either: the runner refuses it
    with its own corrective message, and asking for a full re-issue would
    only reproduce the same oversized call.
    """
    for call in calls or []:
        if not isinstance(call, dict):
            return True
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        if not fn.get("name"):
            return True
        raw = fn.get("arguments")
        if raw is None or (isinstance(raw, str) and (not raw.strip() or raw == _ARG_OVERFLOW)):
            continue
        if isinstance(raw, str):
            try:
                json.loads(raw)
            except (json.JSONDecodeError, ValueError, RecursionError):
                return True
    return False


def round_signature(calls: list) -> tuple:
    sig = []
    for call in calls or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        sig.append((str(fn.get("name") or ""), str(fn.get("arguments") or "")))
    return tuple(sorted(sig))


def _complete_json_object(raw: str):
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _bounded_arguments(raw: str) -> str:
    # Never execute a clipped prefix, even when that prefix happens to be JSON.
    return raw if len(raw) <= _ARG_LIMIT_FILE else _ARG_OVERFLOW


def _take_calls(bucket: dict, raw) -> None:
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index", len(bucket)))
        except (TypeError, ValueError, OverflowError):
            idx = len(bucket)
        rec = bucket.setdefault(
            idx,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if item.get("id"):
            rec["id"] = str(item["id"])[:128]
        fn = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = fn.get("name")
        if isinstance(name, str) and name:
            cur = rec["function"]["name"]
            if not cur or name.startswith(cur):
                rec["function"]["name"] = name[:64]
            elif not cur.startswith(name):
                rec["function"]["name"] = (cur + name)[:64]
        args = fn.get("arguments")
        if rec["function"]["arguments"] == _ARG_OVERFLOW:
            continue
        if isinstance(args, dict):
            rec["function"]["arguments"] = _bounded_arguments(json.dumps(args, ensure_ascii=False))
        elif isinstance(args, str) and args:
            if fn.get("name"):
                parsed = _complete_json_object(args)
                if parsed is not None:
                    rec["function"]["arguments"] = _bounded_arguments(args)
                    continue
            rec["function"]["arguments"] = _bounded_arguments(rec["function"]["arguments"] + args)


def _take_thinking(bucket: list, raw) -> None:
    if not isinstance(raw, list):
        return
    for block in raw:
        if not isinstance(block, dict):
            continue
        redacted = str(block.get("redacted_thinking") or "")
        if redacted:
            bucket.append({"redacted_thinking": redacted})
            continue
        if block.get("devin_signature"):
            # Devin signs the turn's whole reasoning; the text streams apart.
            bucket.append({"devin_signature": str(block["devin_signature"]),
                           "devin_signature_type": str(block.get("devin_signature_type") or "")})
            continue
        reasoning_item = block.get("openai_reasoning")
        if isinstance(reasoning_item, dict) and reasoning_item.get("encrypted_content"):
            bucket.append({"openai_reasoning": dict(reasoning_item)})
            continue
        text = str(block.get("thinking") or block.get("text") or "")
        signature = str(block.get("signature") or "")
        # Signed-empty is a valid omitted-display block and must replay.
        if signature:
            bucket.append({"thinking": text, "signature": signature})


def _native_reasoning(reasoning, model, thinking=None):
    """Keep only the reasoning the serving provider will be sent again.

    Preserve native reasoning for backends whose message adapters replay it.
    Claude and Devin retain their signed thinking blocks separately; OpenAI and
    Grok do not replay their visible reasoning text as conversation messages.
    """
    out = {}
    if (model.startswith(("glm-", "openrouter/z-ai/", "kimi-", "k3-", "mimo-", "openrouter/moonshotai/", "openrouter/xiaomi/"))
            or model == "k3"):
        if reasoning:
            out["reasoning_content"] = str(reasoning)
            out["_fuse_reasoning_model"] = model
        return out
    keeps_blocks = model.startswith("claude/") or model.startswith("claude-")
    keeps_text = model.startswith("devin/")
    if not keeps_blocks and not keeps_text:
        # ChatGPT (Codex Responses, store=false): its encrypted reasoning
        # items are the only way the model keeps its reasoning across rounds.
        items = [dict(block) for block in thinking or []
                 if isinstance(block, dict) and isinstance(block.get("openai_reasoning"), dict)]
        if items:
            out["claude_thinking"] = items
            out["_fuse_reasoning_model"] = model
        return out
    blocks = []
    if keeps_text:
        # Devin streams its reasoning as text and signs it once at the end;
        # replay both as one block so the next request carries what the model
        # produced (devin_payload reads the signature back from it).
        signed = next((block for block in thinking or [] if isinstance(block, dict) and block.get("devin_signature")), None)
        if reasoning or signed:
            block = {"thinking": str(reasoning or ""), "signature": str((signed or {}).get("devin_signature") or "")}
            if (signed or {}).get("devin_signature_type"):
                block["signature_type"] = str(signed["devin_signature_type"])
            out["claude_thinking"] = [block]
            out["_fuse_reasoning_model"] = model
        return out
    for block in thinking or []:
        if not isinstance(block, dict):
            continue
        redacted = str(block.get("redacted_thinking") or "")
        if redacted:
            blocks.append({"redacted_thinking": redacted})
            continue
        text = str(block.get("thinking") or block.get("text") or "")
        signature = str(block.get("signature") or "")
        if (text or signature) and (signature or keeps_text):
            blocks.append({"thinking": text, "signature": signature})
    if blocks:
        out["claude_thinking"] = blocks
        out["_fuse_reasoning_model"] = model
    return out
