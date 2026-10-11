"""Spawn, join, and kill child dashboard runs."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from . import chat_runs, chat_store, run_inbox
from .async_work import durable_call
from .chat_store import SpawnCapError
from .hosts import host_by_alias
from .workspace import RESERVED

logger = logging.getLogger(__name__)


BACKGROUND_STARTED = (
    "The task is working in the background. You will be notified automatically when it finishes. "
    "Do not sleep, poll for progress, ask the task for status, or duplicate this task's work. "
    "Continue with non-overlapping work, or briefly tell the user what you launched."
)
FOLLOWUP_DELIVERED = (
    "Follow-up queued for the live subagent. It becomes the subagent's next "
    "user message when its current step ends, and is already visible in its "
    "panel. You will be notified when it finishes. Do not sleep or poll."
)
# Foreground spawn wait before the child becomes a background task. Product-
# tuned and pinned by a contract test; the wake path covers longer children.
FOREGROUND_BUDGET_S = 60.0
_MODEL_RESULT_CHARS = 6_000
_PROVIDER_ALIASES = {
    "grok": "grok",
    "xai": "grok",
    "x.ai": "grok",
    "x-ai": "grok",
    "chatgpt": "chatgpt",
    "openai": "chatgpt",
    "chat-gpt": "chatgpt",
    "zai": "zai",
    "z.ai": "zai",
    "z-ai": "zai",
    "glm": "zai",
    "devin": "devin",
    "cascade": "devin",
    "windsurf": "devin",
    "openrouter": "openrouter",
    "explabs": "explabs",
    "experiential-labs": "explabs",
    "ollama": "ollama",
    "llama": "llama",
    "llama.cpp": "llama",
}


def _norm_model_query(raw: str) -> str:
    text = (raw or "").strip().lower()
    text = text.replace("openrouter/", "")
    text = text.replace("devin/", "")
    return re.sub(r"[\s_]+", "-", text).strip("-")


def _alias_type(token: str) -> str:
    key = (token or "").strip().lower()
    if not key:
        return ""
    if key in _PROVIDER_ALIASES:
        return _PROVIDER_ALIASES[key]
    compact = key.replace(".", "").replace("-", "")
    for alias, type_id in _PROVIDER_ALIASES.items():
        if alias.replace(".", "").replace("-", "") == compact:
            return type_id
    return ""


def _query_parts(query: str) -> tuple[str, set[str]]:
    """Split a search into (model rest, provider ids mentioned in the query)."""
    q = _norm_model_query(query)
    if not q:
        return "", set()
    whole = _alias_type(q)
    if whole:
        return "", {whole}
    providers: set[str] = set()
    kept: list[str] = []
    tokens = [t for t in re.split(r"[-/]+", q) if t]
    i = 0
    while i < len(tokens):
        token = tokens[i]
        kind = _alias_type(token)
        if not kind and i + 1 < len(tokens):
            kind = _alias_type(token + "-" + tokens[i + 1])
            if kind:
                providers.add(kind)
                i += 2
                continue
        if kind:
            providers.add(kind)
        else:
            kept.append(token)
        i += 1
    return "-".join(kept), providers


def _item_providers(item: dict) -> set[str]:
    type_id = str(item.get("type") or "").lower()
    mid = str(item.get("id") or "").lower()
    if mid.startswith("explabs/") or type_id == "explabs":
        return {"explabs"}
    if mid.startswith("devin/") or type_id == "devin":
        return {"devin"}
    out: set[str] = set()
    if type_id:
        out.add(type_id)
    if mid.startswith("openrouter/"):
        out.add("openrouter")
        parts = mid.split("/")
        if len(parts) >= 3:
            mapped = _alias_type(parts[1])
            if mapped:
                out.add(mapped)
    elif type_id == "openrouter" and "/" in mid:
        mapped = _alias_type(mid.split("/", 1)[0])
        if mapped:
            out.add(mapped)
    if type_id == "zai" or mid.startswith("glm-"):
        out.add("zai")
    return out


def _stem(model_id: str) -> str:
    text = (model_id or "").strip().lower()
    if text.startswith("openrouter/"):
        text = text.split("/", 2)[-1]
    elif text.startswith("devin/"):
        text = text[6:]
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text


async def _catalog_entries(user_id: int) -> list[dict]:
    from . import opencode as opencode_mod

    return await opencode_mod.model_options(user_id)


def _match_score(item: dict, q: str, providers: set[str]) -> int | None:
    mid = str(item.get("id") or "").lower()
    if not mid:
        return None
    if providers and not providers.intersection(_item_providers(item)):
        return None
    type_id = str(item.get("type") or "")
    name = str(item.get("name") or "").lower()
    stem = _stem(mid)
    blob = " ".join((mid, name, type_id, stem))
    compact = blob.replace("/", "").replace(".", "").replace("-", "").replace(" ", "")
    if not q:
        return 2
    q_compact = q.replace(".", "").replace("-", "")
    if mid == q or mid.endswith("/" + q):
        return 0
    if stem == q:
        return 1
    if q_compact and stem.replace(".", "").replace("-", "") == q_compact:
        return 1
    if stem.startswith(q) or q in stem or q in mid:
        return 3
    if q in blob or (q_compact and q_compact in compact):
        return 4
    tokens = [t.replace(".", "") for t in q.split("-") if len(t) >= 2]
    if len(tokens) > 1 and all(t in compact for t in tokens):
        return 4
    if tokens and all(t in compact for t in tokens):
        return 4
    return None


def _match_entries(entries: list[dict], query: str) -> list[dict]:
    rest, providers = _query_parts(query)
    if not rest and not providers:
        return []
    ranked: list[tuple[int, dict]] = []
    seen: set[str] = set()
    for item in entries:
        mid = str(item.get("id") or "")
        if not mid or mid in seen:
            continue
        score = _match_score(item, rest, providers)
        if score is None:
            continue
        seen.add(mid)
        ranked.append((score, {"id": mid, "name": str(item.get("name") or mid), "provider": str(item.get("type") or "")}))
    ranked.sort(key=lambda row: (row[0], row[1]["id"]))
    return [row[1] for row in ranked]


def _exact_entry(entries: list[dict], slug: str) -> dict | None:
    want = (slug or "").strip()
    if not want:
        return None
    for item in entries:
        if str(item.get("id") or "") == want:
            return item
    lowered = want.lower()
    for item in entries:
        if str(item.get("id") or "").lower() == lowered:
            return item
    return None


def _ambiguous_hint(hits: list[dict]) -> str:
    providers = sorted({h["provider"] for h in hits if h.get("provider")})
    if len(providers) <= 1:
        return ""
    labels = ", ".join(providers)
    return (
        f"Several providers match ({labels}). Ask the user which provider to use, "
        "then spawn_subagent with that exact id."
    )


def _pack_model_search(query: str, hits: list[dict], *, providers: list[dict] | None = None) -> str:
    payload: dict = {
        "query": query,
        "models": hits,
        "usage": (
            "Pass an exact id as spawn_subagent model. "
            "If several providers are listed for the same model, ask the user which one."
        ),
    }
    hint = _ambiguous_hint(hits)
    if hint:
        payload["conflict"] = hint
    if providers is not None:
        payload["providers"] = providers
        payload["usage"] = (
            "Call search_models again with a provider or model name (grok, glm, gpt-5.4). "
            "Then pass an exact returned id as spawn_subagent model."
        )
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= _MODEL_RESULT_CHARS:
        return text
    payload["models"] = hits[:48]
    payload["note"] = "truncated; refine the query"
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= _MODEL_RESULT_CHARS:
        return text
    payload["models"] = hits[:24]
    return json.dumps(payload, ensure_ascii=False)[:_MODEL_RESULT_CHARS]


async def search_models(*, user_id: int, arguments: str) -> tuple[str, dict]:
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}
    query = str(args.get("query") or args.get("q") or "").strip()
    try:
        entries = await _catalog_entries(user_id)
    except Exception:
        msg = "model catalog unavailable"
        return msg, {"name": "search_models", "status": "error", "error": msg, "query": query, "preview": msg}
    if not query:
        counts: dict[str, int] = {}
        for item in entries:
            type_id = str(item.get("type") or "unknown")
            counts[type_id] = counts.get(type_id, 0) + 1
        providers = [{"id": k, "count": counts[k]} for k in sorted(counts)]
        text = _pack_model_search("", [], providers=providers)
        return text, {"name": "search_models", "status": "done", "query": "", "preview": "providers"}
    hits = _match_entries(entries, query)
    if not hits:
        text = json.dumps(
            {
                "query": query,
                "models": [],
                "usage": "No models matched. Try a provider name (grok, chatgpt, zai, openrouter, explabs) or a model family.",
            },
            ensure_ascii=False,
        )
        return text, {"name": "search_models", "status": "done", "query": query, "preview": "none"}
    if len(hits) > 48:
        hits = hits[:48]
    text = _pack_model_search(query, hits)
    preview = hits[0]["id"] if len(hits) == 1 else f"{len(hits)} models"
    rec = {"name": "search_models", "status": "done", "query": query, "preview": preview[:800]}
    if _ambiguous_hint(hits):
        rec["kind"] = "conflict"
    return text, rec


async def resolve_spawn_model(*, user_id: int, slug: str, parent_model: str) -> tuple[str, str]:
    """Return (model_id, error). Empty error means spawn with model_id (possibly parent)."""
    want = (slug or "").strip()
    if not want:
        return parent_model or "", ""
    parent = (parent_model or "").strip()
    if want == parent:
        return parent, ""
    try:
        entries = await _catalog_entries(user_id)
    except Exception:
        return "", "model catalog unavailable; omit model to clone this agent, or retry search_models"
    exact = _exact_entry(entries, want)
    if exact:
        return str(exact["id"]), ""
    hits = _match_entries(entries, want)
    _, providers = _query_parts(want)
    if "explabs" not in providers:
        hits = [h for h in hits if h["provider"] != "explabs"]
    if len(hits) == 1:
        return hits[0]["id"], ""
    if not hits:
        return (
            "",
            f"Unknown model '{want}'. Call search_models and pass an exact returned id, or omit model to clone this agent.",
        )
    ids = ", ".join(h["id"] for h in hits[:12])
    extra = "…" if len(hits) > 12 else ""
    hint = _ambiguous_hint(hits)
    if hint:
        return "", f"Ambiguous model '{want}'. {hint} Matches: {ids}{extra}."
    return (
        "",
        f"Ambiguous model '{want}'. Call search_models and pass an exact id. Matches: {ids}{extra}.",
    )


def _same_host(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


async def _review_child_cwd(user_id: int, pagent: dict, host_id, cwd: str, gate) -> tuple[str, str]:
    """A child's cwd becomes its working root, i.e. its allowed folder. Review
    it against the parent's own scope, asking the user exactly as the parent
    would for that folder. Returns (resolved cwd, error)."""
    from . import permissions
    from .hosts import get_host
    from .workspace import _is_windows_row, host_root

    if host_id is None:
        return "", "cwd needs a host: bind a project or pass host with cwd."
    row = await asyncio.to_thread(get_host, user_id, host_id)
    if not row or row.get("disabled"):
        return "", "unknown or disabled host for cwd."
    windows = _is_windows_row(row)
    parent_root = host_root(row, pagent.get("bound_host_id"), str(pagent.get("bound_cwd") or ""))
    resolved = permissions._resolve_for_scope(cwd, parent_root or "~", windows)
    if resolved.startswith(".."):
        return "", "cwd escapes the home directory."
    if gate is None:
        ok, _ = permissions.scope_allows(cwd, parent_root or "~", permissions.compute_roots(parent_root, windows), windows)
        if not ok:
            return "", "cwd is outside this agent's allowed folders."
        return resolved, ""
    verdict = await permissions.review(
        tool="spawn_subagent", args={"cwd": cwd}, user_id=user_id, host_id=int(row["id"]),
        host_alias=str(row.get("alias") or ""), root=parent_root, windows=windows, gate=gate,
    )
    if not verdict.allowed:
        return "", verdict.detail or "cwd is outside this agent's allowed folders."
    return resolved, ""


async def _hand_off(user_id: int, parent_run_id: str, child_id: str, description: str) -> None:
    """Make a child the parent was told is "running" report in the background.

    The child's finish path only queues or wakes for background children. If
    it finished between the foreground wait expiring and this mark, it saw
    background=False and queued nothing, so deliver its report here.
    """
    await durable_call(chat_store.mark_child_background, child_id)
    row = await asyncio.to_thread(chat_store.get_run, user_id, child_id)
    if row is not None and row.get("status") in chat_store.TERMINAL:
        await durable_call(
            chat_store.queue_child_result, parent_run_id, _envelope(row, description=description), child_id=child_id
        )


def _child_summary(row: dict) -> str:
    """Completed report or status-only notification from the shared delivery policy."""
    text = chat_store.child_writeup(row)
    if text:
        return text
    return "Subagent finished without a report."


def _envelope(row: dict | None, *, fallback_id: str = "", description: str = "", running: bool = False) -> str:
    if running:
        return chat_store.render_task_output(
            child_id=fallback_id,
            state="running",
            text=BACKGROUND_STARTED,
            description=description or "Background task started",
        )
    if row is None:
        return chat_store.render_task_output(
            child_id=fallback_id,
            state="error",
            text="Subagent failed to start.",
            description=description,
        )
    status = str(row.get("status") or "")
    if status in chat_store.ACTIVE:
        return chat_store.render_task_output(
            child_id=row["id"],
            state="running",
            text=BACKGROUND_STARTED,
            description=description or str(((row.get("request") or {}).get("agent") or {}).get("description") or ""),
        )
    text = _child_summary(row)
    state = "completed" if status == "completed" else ("interrupted" if status in ("cancelled", "interrupted") else "error")
    desc = description
    if not desc:
        agent = (row.get("request") or {}).get("agent") if isinstance(row.get("request"), dict) else {}
        desc = str((agent or {}).get("description") or "")
    generation = str(((row.get("request") or {}).get("agent") or {}).get("delivery_generation") or "")
    return chat_store.render_task_output(child_id=row["id"], state=state, text=text, description=desc, generation=generation)


async def spawn(
    *,
    user,
    request,
    parent_run_id: str,
    arguments: str,
    thread_id: int,
    on_started=None,
    gate=None,
) -> tuple[str, dict]:
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}
    prompt = str(args.get("prompt") or "").strip()
    scratch_name = str(args.get("scratch") or "").strip() if isinstance(args.get("scratch"), str) else ""
    description = str(args.get("description") or "").strip()[:200]
    followup = any(isinstance(args.get(key), str) and args.get(key).strip() for key in ("task_id", "resume_from"))
    if not description and not followup:
        # Claude Code's Agent tool requires it: the label is the row's title.
        msg = "description is required: a short (3-5 word) description of the task"
        return msg, {"name": "spawn_subagent", "status": "error", "error": msg, "kind": "invalid_args"}
    raw_model = args.get("model")
    requested_model = str(raw_model).strip() if isinstance(raw_model, str) else ""
    parent = await asyncio.to_thread(chat_store.get_run, user["id"], parent_run_id)
    if parent is None:
        return "parent run is not active", {"name": "spawn_subagent", "status": "error", "error": "parent run is not active"}
    if scratch_name:
        # The slot is the prompt. A failed spawn leaves it in place, so the
        # same call can be retried without the model re-sending the text.
        stored = await asyncio.to_thread(chat_store.scratch_read, parent_run_id, scratch_name)
        if stored is None:
            msg = f"scratch slot {scratch_name!r} not found; write it with the scratch tool first."
            return msg, {"name": "spawn_subagent", "status": "error", "error": msg, "kind": "not_found", "description": description}
        prompt = stored.strip()
    if not prompt:
        return "prompt is required", {"name": "spawn_subagent", "status": "error", "error": "prompt is required", "description": description}
    pagent = (parent.get("request") or {}).get("agent") if isinstance(parent.get("request"), dict) else {}
    pagent = pagent if isinstance(pagent, dict) else {}
    nested = bool(pagent.get("allow_nested_spawn"))
    parent_kind = str(pagent.get("subagent_type") or "")
    if parent_kind in ("explore", "plan"):
        # Restricted children must not spawn a less-restricted grandchild.
        nested = False
    depth = int(pagent.get("depth") or 0) + 1
    # Children get a full turn budget (same 8h safety cap as the parent).
    remaining = float(chat_store.DEFAULT_REMAINING_SECONDS)
    host_id = pagent.get("bound_host_id")
    alias = args.get("host") if isinstance(args.get("host"), str) else ""
    if alias:
        if alias.strip().lower() in RESERVED:
            return "host alias is not allowed", {"name": "spawn_subagent", "status": "error", "error": "host alias is not allowed", "description": description}
        row = await asyncio.to_thread(host_by_alias, user["id"], alias)
        if row is None:
            return "unknown host alias", {"name": "spawn_subagent", "status": "error", "error": "unknown host alias", "description": description}
        host_id = row["id"]
    explicit_cwd = str(args.get("cwd") or "").strip() if isinstance(args.get("cwd"), str) else ""
    if explicit_cwd:
        cwd, cwd_error = await _review_child_cwd(user["id"], pagent, host_id, explicit_cwd, gate)
        if cwd_error:
            return cwd_error, {
                "name": "spawn_subagent",
                "status": "error",
                "error": cwd_error,
                "kind": "permission_denied",
                "description": description,
            }
    elif _same_host(host_id, pagent.get("bound_host_id")):
        cwd = str(pagent.get("bound_cwd") or "")
    else:
        # Another host: the parent's folder is a path on a different machine.
        # The child uses that host's own default working directory.
        cwd = ""
    background = bool(args.get("background"))
    resume_from = args.get("task_id") if isinstance(args.get("task_id"), str) else None
    if not resume_from and isinstance(args.get("resume_from"), str):
        resume_from = args.get("resume_from")
    if resume_from and not description:
        existing_label = await asyncio.to_thread(chat_store.get_run, user["id"], resume_from)
        existing_agent = ((existing_label.get("request") or {}).get("agent") or {}) if existing_label else {}
        description = str(existing_agent.get("description") or "subagent").strip()[:200] or "subagent"
    if resume_from:
        existing = await asyncio.to_thread(chat_store.get_run, user["id"], resume_from)
        if existing and existing.get("status") in chat_store.ACTIVE:
            root = pagent.get("root_run_id") or parent_run_id
            # Follow-ups only make sense for a live child; pending_followups on
            # a root/parent run is never drained.
            same_tree = bool(existing.get("parent_run_id")) and (
                existing.get("root_run_id") == root or existing.get("parent_run_id") == parent_run_id
            )
            if not same_tree and existing.get("parent_run_id") and not parent.get("parent_run_id"):
                # A background child outlives the turn that spawned it; the
                # main agent's later turns are new root runs but still own
                # every subagent in the chat.
                same_tree = str(existing.get("thread_id")) == str(parent.get("thread_id"))
            if same_tree and parent.get("parent_run_id"):
                # A subagent may steer only its own descendants, never a
                # sibling outside its delegation (same rule as get/kill/list).
                same_tree = await asyncio.to_thread(_descends_from, user["id"], existing, parent_run_id)
            if not same_tree:
                return "task_id is not a child of this session.", {
                    "name": "spawn_subagent",
                    "status": "error",
                    "error": "task_id is not a child of this session.",
                    "description": description,
                }
            existing_agent = (existing.get("request") or {}).get("agent") or {}
            target_kind = str(existing_agent.get("subagent_type") or "general-purpose")
            if parent_kind in ("explore", "plan") and target_kind != parent_kind:
                msg = "Restricted subagents cannot steer an agent of a different type."
                return msg, {"name": "spawn_subagent", "status": "error", "error": msg, "preview": msg}
            if requested_model:
                existing_model = str((existing.get("request") or {}).get("model") or "")
                if requested_model != existing_model:
                    msg = "Cannot change model on a live follow-up; omit model or wait until the child finishes."
                    return msg, {
                        "name": "spawn_subagent",
                        "status": "error",
                        "error": msg,
                        "preview": msg,
                        "child_id": existing["id"],
                        "description": description,
                    }
            if not chat_store.queue_followup(existing["id"], prompt):
                msg = (
                    "Live subagent did not accept the follow-up (it may have just finished, "
                    "be stopping, or its follow-up queue is full). Wait for the completion "
                    "notice, then continue it with spawn_subagent task_id."
                )
                return msg, {
                    "name": "spawn_subagent",
                    "status": "error",
                    "error": msg,
                    "child_id": existing["id"],
                    "description": description,
                }
            text = chat_store.render_task_output(
                child_id=existing["id"],
                state="running",
                text=FOLLOWUP_DELIVERED,
                description=description or "Follow-up",
            )
            rec = {
                "name": "spawn_subagent",
                "status": "running",
                "child_id": existing["id"],
                "description": description,
                "preview": description,
                "summary": "Follow-up delivered",
                "background": True,
            }
            existing_model = str((existing.get("request") or {}).get("model") or "")
            if existing_model:
                rec["model"] = existing_model
            return text, rec
    parent_model = str((parent.get("request") or {}).get("model") or "")
    existing_resume = await asyncio.to_thread(chat_store.get_run, user["id"], resume_from) if resume_from else None
    if existing_resume and not requested_model:
        parent_model = str((existing_resume.get("request") or {}).get("model") or "") or parent_model
    if parent_kind in ("explore", "plan"):
        spawn_kind = parent_kind
    else:
        spawn_kind = str(args.get("subagent_type") or "").strip()
        if spawn_kind not in ("general-purpose", "explore", "plan"):
            # Omitted on resume: keep the session's type (an explore auditor
            # stays read-only unless the parent explicitly promotes it).
            spawn_kind = "general-purpose"
            if existing_resume:
                resume_agent = (existing_resume.get("request") or {}).get("agent")
                spawn_kind = str((resume_agent or {}).get("subagent_type") or "") or spawn_kind
    model, model_error = await resolve_spawn_model(
        user_id=user["id"],
        slug=requested_model,
        parent_model=parent_model,
    )
    if model_error:
        return model_error, {
            "name": "spawn_subagent",
            "status": "error",
            "error": model_error,
            "preview": model_error[:800],
            "description": description,
            "kind": "unknown_model",
        }
    admitted = []

    def admit_child():
        child = chat_store.start_child_run(
            user_id=user["id"],
            thread_id=thread_id or parent["thread_id"],
            parent_run_id=parent_run_id,
            prompt=prompt,
            description=description,
            subagent_type=spawn_kind,
            host_id=host_id,
            cwd=cwd,
            model=model,
            depth=depth,
            remaining_seconds=remaining,
            resume_from_id=resume_from,
            permission_mode=str(pagent.get("permission_mode") or "project"),
            max_completions=0,
            allow_nested_spawn=nested,
            background=background,
            auto=bool(pagent.get("auto")),
            scheduled=bool(pagent.get("scheduled")),
        )
        admitted.append(child)
        return child

    try:
        child = await durable_call(admit_child)
    except asyncio.CancelledError:
        # Stop may land between admission committing and start(). Hand the
        # queued row to its worker as the pre-await path would have; a row
        # that cannot start now stays queued — recovery/resume_queued owns
        # queued runs, and background children must survive a parent Stop.
        if admitted:
            try:
                await chat_runs.start(request, user, admitted[0])
            except (Exception, asyncio.CancelledError):
                logger.exception("Subagent handoff after cancellation failed for %s", admitted[0].get("id"))
        raise
    except SpawnCapError as exc:
        return exc.detail, {"name": "spawn_subagent", "status": "error", "error": exc.detail, "preview": exc.detail[:800], "description": description}
    def _spawn_rec(**extra) -> dict:
        rec = {
            "name": "spawn_subagent",
            "child_id": child["id"],
            "description": description,
            "preview": description,
            "subagent_type": spawn_kind,
            **extra,
        }
        if model:
            rec["model"] = model
        return rec

    if on_started:
        on_started(_spawn_rec(status="running", background=background))
    try:
        task = await chat_runs.start(request, user, child)
    except Exception as exc:
        logger.exception("Subagent start failed for %s", child["id"])
        err = str(exc).strip()[:400] or "Subagent could not start"
        await durable_call(chat_store.abort_active, child["id"], "failed", err)
        await durable_call(chat_store.refund_spawn, parent_run_id)
        return err, _spawn_rec(status="error", error=err, preview=err[:800])
    current = await asyncio.to_thread(chat_store.get_run, user["id"], child["id"]) if task is None else child
    if task is None and current is not None and current.get("status") not in chat_store.ACTIVE:
        await durable_call(chat_store.refund_spawn, parent_run_id)
    if background and current and current.get("status") in chat_store.ACTIVE:
        text = _envelope(child, fallback_id=child["id"], description=description, running=True)
        return text, _spawn_rec(
            status="running",
            summary="Background task started",
            background=True,
        )
    if task is not None:
        finished, arrival = await run_inbox.wait_or_arrival(
            task, FOREGROUND_BUDGET_S, parent_run_id, ignore={child["id"]}
        )
        if not finished:
            # Out of foreground budget, or other results arrived for the
            # parent: either way the child carries on and reports later.
            await _hand_off(user["id"], parent_run_id, child["id"], description)
            text = _envelope(child, fallback_id=child["id"], description=description, running=True)
            if arrival:
                text = f"{run_inbox.EARLY_RETURN_NOTE}\n{text}"
            return text, _spawn_rec(
                status="running",
                summary="Background task started",
                background=True,
            )
        # Cancellation of the child is a child outcome, not parent cancellation.
        exc = None if task.cancelled() else task.exception()
        if exc is not None:
            err = str(exc)[:400]
            return err, _spawn_rec(status="error", error=err, preview=err[:800])
    done = await asyncio.to_thread(chat_store.get_run, user["id"], child["id"])
    summary = _child_summary(done) if done else ""
    status = "error"
    if done:
        status = "done" if done.get("status") == "completed" else "error"
        if done.get("status") in chat_store.ACTIVE:
            # The worker returned but the run continues (a follow-up
            # continuation re-queued it): it now reports in the background.
            status = "running"
            await _hand_off(user["id"], parent_run_id, child["id"], description)
        if done.get("status") in ("cancelled", "interrupted"):
            status = "interrupted"
        if done["status"] in chat_store.TERMINAL:
            # Returning a tool result is not durable delivery. Keep a receipt
            # pending until save_turn_progress saves this exact envelope.
            await durable_call(chat_store.mark_child_background, child["id"])
            await durable_call(chat_store.queue_child_result, parent_run_id, _envelope(done, description=description), child_id=child["id"], notify=False)
    text = _envelope(done, fallback_id=child["id"], description=description)
    work = {"agent_work_ms": _work_ms(done)} if done and done.get("status") in chat_store.TERMINAL else {}
    return text, _spawn_rec(
        status=status,
        **work,
        summary=summary[:2000],
        preview=(summary or description)[:800],
        error="" if status in ("done", "running") else (done.get("status") if done else "failed"),
        background=background or status == "running",
    )


def _work_ms(run: dict) -> int:
    """The child's cumulative active time (idle gaps between legs excluded)."""
    req = run.get("request") if isinstance(run.get("request"), dict) else {}
    return chat_store.run_work_ms(run["id"], req.get("agent"), run.get("message"))


def _descends_from(user_id: int, row: dict | None, ancestor_id: str) -> bool:
    """True when row sits below ancestor_id in its spawn tree."""
    current = row
    for _ in range(16):
        parent = str((current or {}).get("parent_run_id") or "")
        if not parent:
            return False
        if parent == str(ancestor_id):
            return True
        current = chat_store.get_run(user_id, parent)
    return False


async def get_output(*, user_id: int, arguments: str, thread_id=None, parent_run_id: str | None = None,
                     scope_run_id: str | None = None) -> tuple[str, dict]:
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}
    child_id = str(args.get("id") or args.get("child_id") or "")
    timeout_ms = args.get("timeout_ms")
    try:
        # Join blocks until the child finishes. The old 60s default forced the
        # model into a re-poll loop against long background agents; tools run
        # concurrently in the parent's TaskGroup now, so a long join is cheap
        # and the default is the full 600s cap.
        wait = min(max(int(timeout_ms), 0), 600_000) / 1000 if timeout_ms is not None else 600
    except (TypeError, ValueError):
        wait = 600
    deadline = time.monotonic() + wait
    row = None
    misses = 0
    early = ""
    while True:
        row = await asyncio.to_thread(chat_store.resolve_child_run,
            user_id,
            child_id,
            thread_id=thread_id,
            parent_run_id=parent_run_id,
        )
        if row is not None and scope_run_id and not await asyncio.to_thread(_descends_from, user_id, row, scope_run_id):
            row = None
            break
        if row is None:
            misses += 1
            if misses >= 5 or time.monotonic() >= deadline:
                # A resolvable child row exists before the tool can reference
                # it; a few misses covers replica lag, not a typo'd id.
                break
            await asyncio.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
            continue
        misses = 0
        child_id = row["id"]
        if row["status"] not in chat_store.ACTIVE:
            summary = _child_summary(row)
            text = _envelope(row)
            if parent_run_id:
                await durable_call(chat_store.mark_child_background, child_id)
                await durable_call(chat_store.queue_child_result, str(parent_run_id), text, child_id=child_id, notify=False)
            status = "done" if row["status"] == "completed" else "error"
            if row["status"] in ("cancelled", "interrupted"):
                status = "interrupted"
            return text, {
                "name": "get_subagent_output",
                "status": status,
                "child_id": child_id,
                "agent_work_ms": _work_ms(row),
                "description": str(((row.get("request") or {}).get("agent") or {}).get("description") or "")[:200],
                "summary": summary[:2000],
                "preview": summary[:800],
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0 or early:
            break
        # Other results arriving for the caller end the join early so the
        # model sees them now; this child keeps running and reports later.
        _finished, early = await run_inbox.wait_or_arrival(
            chat_runs.wait_for_run(child_id, remaining), remaining, parent_run_id, ignore={child_id}
        )
    if row is None:
        return "subagent not found", {"name": "get_subagent_output", "status": "error", "error": "not found", "child_id": child_id}
    text = _envelope(row, fallback_id=child_id, running=True)
    if early:
        text = f"{run_inbox.EARLY_RETURN_NOTE}\n{text}"
    return text, {
        "name": "get_subagent_output",
        "status": "running",
        "child_id": child_id,
        "description": str(((row.get("request") or {}).get("agent") or {}).get("description") or "")[:200],
        "preview": "still running",
        "background": True,
        # How long this join waits before it gives up and reports the child
        # still running. The child itself has no deadline.
        "timeout_ms": int(wait * 1000),
    }


async def kill(*, user_id: int, arguments: str, thread_id=None, scope_run_id: str | None = None) -> tuple[str, dict]:
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}
    child_id = str(args.get("id") or args.get("child_id") or "")
    row = await asyncio.to_thread(chat_store.resolve_child_run, user_id, child_id, thread_id=thread_id)
    if row is not None and scope_run_id and not await asyncio.to_thread(_descends_from, user_id, row, scope_run_id):
        row = None
    if row is None:
        return "subagent not found", {"name": "kill_subagent", "status": "error", "error": "not found", "child_id": child_id}
    child_id = row["id"]
    agent = (row.get("request") or {}).get("agent") if isinstance(row.get("request"), dict) else None
    label = str((agent or {}).get("description") or "")[:200] if isinstance(agent, dict) else ""
    named = {"description": label} if label else {}
    if row["status"] not in chat_store.ACTIVE:
        return "subagent already finished", {
            "name": "kill_subagent",
            "status": "done",
            "child_id": child_id,
            "preview": f"already {row['status']}",
            **named,
        }
    ids = await durable_call(chat_store.request_cancel_self, user_id, child_id)
    if not ids:
        return "subagent not found", {"name": "kill_subagent", "status": "error", "error": "not found", "child_id": child_id}
    for ident in ids:
        chat_runs.cancel(ident)
    return "cancelled", {"name": "kill_subagent", "status": "done", "child_id": child_id, **named}


async def list_agents(*, user_id: int, thread_id, scope_run_id: str | None = None) -> tuple[str, dict]:
    if thread_id is None:
        return "no active session", {"name": "list_subagents", "status": "error", "error": "no active session"}
    rows = await asyncio.to_thread(chat_store.list_thread_agents, user_id, thread_id)
    if scope_run_id:
        below = {str(scope_run_id)}
        grew = True
        while grew:
            grew = False
            for item in rows:
                if str(item.get("parent_run_id") or "") in below and str(item["id"]) not in below:
                    below.add(str(item["id"]))
                    grew = True
        rows = [item for item in rows if str(item["id"]) in below and str(item["id"]) != str(scope_run_id)]
    live = [item for item in rows if item["status"] in chat_store.ACTIVE]
    finished = [item for item in rows if item["status"] not in chat_store.ACTIVE]

    def _entry(item: dict) -> dict:
        out = {
            "id": item["id"],
            "description": item["description"],
            "subagent_type": item["subagent_type"],
            "status": item["status"],
            "depth": item["depth"],
            "children": item["children"],
        }
        if item.get("model"):
            out["model"] = item["model"]
        if item.get("error"):
            out["error"] = item["error"][:200]
        summary = (item.get("summary") or "").strip()
        if summary:
            out["summary"] = summary[:300]
        return out

    shown = finished[:32]
    payload = {
        "live": [_entry(item) for item in live],
        "finished": [_entry(item) for item in shown],
        "usage": (
            "Background children keep running after your turn ends and wake you with their results "
            "when they finish. To continue one (after an error, usage limit, model switch, restart, "
            "or in a later turn), spawn_subagent again with task_id=<id> to continue "
            "the same subagent session — this includes children in failed or interrupted state. "
            "task_id resumes that child; it is not a name. A spawn that failed before it "
            "returned an id is retried as a new spawn, not resumed. "
            "A live child receives a follow-up instead; do not poll for status."
        ),
    }
    failed_ids = [
        item["id"] for item in finished
        if item["status"] in ("failed", "interrupted", "cancelled")
    ][:32]
    if failed_ids:
        payload["failed_ids"] = failed_ids
    if len(finished) > len(shown):
        payload["omitted_finished"] = len(finished) - len(shown)
    text = _fit_list_json(payload)
    return text, {
        "name": "list_subagents",
        "status": "done",
        "preview": f"{len(live)} live, {len(shown)} finished",
    }


def _fit_list_json(payload: dict, limit: int = 6000) -> str:
    """Serialize within limit by dropping finished entries (never live ones)
    before falling back to a bare id list - a hard string slice would cut the
    JSON mid-token and the model could not parse the tool result."""
    def dump() -> str:
        return json.dumps(payload, ensure_ascii=False)

    text = dump()
    while len(text) > limit and payload.get("finished"):
        payload["finished"].pop()
        payload["omitted_finished"] = int(payload.get("omitted_finished") or 0) + 1
        text = dump()
    if len(text) <= limit:
        return text
    bare = {
        "live": [item["id"] for item in payload.get("live") or []],
        "finished": [item["id"] for item in payload.get("finished") or []],
        "note": "subagent list too large; use get_subagent_output with an id",
    }
    return json.dumps(bare, ensure_ascii=False)
