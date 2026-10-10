from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import math
import re

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from . import api, artifacts, chats, context_compact as ctxmod, context_meter
from .chat_generation import inbox, metadata, policy, tool_runner
from .chat_tools import execute_tool, openai_tools, parse_tool_names
from .config import settings
from .proxy import merge_usage, usage_from_obj
from .sources import POOL_TYPES

from .chat_prompts import (
    INIT_PROMPT,
    RECAP_SYSTEM,
    TITLE_SYSTEM,
    build_system_prompt,
    expand_slash_messages,
    sanitize_messages,
    _clip,
)

# Stable re-exports. External modules (chat_runs, chat_store, chat_titles,
# chat_recaps, command_jobs, web.chat) and the test suite pin these names on
# app.chat_runtime; the implementations live in app.chat_generation. Keep these
# aliases until those callers import from the new modules directly.
from .chat_generation.parsing import (
    _ARG_LIMIT_ARTIFACT,
    _finalize_assistant_payload,
    _native_reasoning,
    _take_calls,
    _xml_call,
    calls_incomplete,
    drop_tool_xml,
    extract_text_tool_calls,
    round_signature,
    strip_special_tokens,
)
from .chat_generation.stream import (
    CHUNK_GAP_TIMEOUT_S,
    HEARTBEAT_S,
    IDLE_TIMEOUT_S,
    PROVIDER_BEAT_S,
    _heartbeat_sleep,
    _paced_chunks,
    _sse,
    _terminated_chunks,
    _with_beats,
)
from .chat_generation.policy import (
    IDLE_STALL_RETRIES,
    MAX_RESUME_RETRIES,
    _RESUME_BACKOFF,
    _RESUME_MAX_WAIT,
    _RESUME_NOTE,
    _RESUME_TOOLS_NOTE,
    _TOOLS_UNSUPPORTED,
    _apply_resume_state,
    _cap_set,
    _error_code_and_message,
    _error_obj,
    _exhausted_provider_error,
    _header_retry_after,
    _is_idle_timeout,
    _model_type_guess,
    _promote_provider_content_error,
    _provider_error_content,
    _resumable_failure,
    _should_resume,
)
from .chat_generation import cache_watch
from .chat_generation.consume import _add_usage, consume_round as _consume_round
from .chat_generation.tool_jobs import (
    CODING_UNLIMITED,
    READ_RESULT_CHARS,
    SKILL_RESULT_CHARS,
    SUBAGENT_RESULT_CHARS,
    TOOL_RESULT_CHARS,
    ToolJob,
    _coding_completion_limit,
    _completion_start_index,
    _is_preload_tool,
    _provider_snapshot,
    _redact_tool_args,
    _running_event,
    _tool_messages,
    _tool_result_clip,
    _tool_rounds_done,
    apply_skill_preload,
    fold_preload_skill,
    jobs_from_calls,
)
from .chat_generation.tool_runner import (
    MAX_CALLS_PER_ROUND,
    _record_failure,
)
from .chat_generation.messages import (
    _artifact_lines,
    _drop_tools,
    _inject_session_tail,
    _latest_user_text,
    _retrieval_first,
    _session_tail_block,
    _user_public_urls,
    inject_web_context,
)

logger = logging.getLogger(__name__)


MAX_COMPLETIONS = 6


# Consecutive failed tool calls (parent and child). A real audit may run
# dozens of successful rounds; this only trips when calls fail back-to-back.
STALL_FAIL_CALLS = 20


MAX_LENGTH_SALVAGES = 2
# Empty responses after tool results get a "write the answer now" nudge; a
# provider that keeps returning empty must not be retried forever.
MAX_EMPTY_RETRIES = 3
STATIONARITY_NUDGE = 8
STATIONARITY_HALT = 12


_NUDGE = (
    "[Fuse] Wrap-up notice for the reply in progress — it applies to that reply only; "
    "tools stay available for later replies. The tool-round budget for this reply is exhausted. "
    "Reply with text only: say that the step budget was reached, summarize what was done, "
    "list anything not finished, and recommend what should happen next. "
    "Do not claim the task is complete if work remains. Do not call tools for this reply. "
    "Server-added notice, not user input."
)
_STALL_WRAP = (
    "[Fuse] CRITICAL — tools are now disabled for this reply. Do not call tools. "
    "Write the user-facing answer from the evidence already gathered: what you found, "
    "what is unfinished, and what should happen next. Do not claim the task is complete "
    "if work remains. Server-added notice, not user input."
)
_CHILD_CONTINUE = (
    "[Fuse] The tools already ran. Continue the assigned task. Call more tools if you still "
    "need evidence; otherwise write your final report for the parent now. The parent "
    "only sees that final text, not these tool traces. Server-added notice, not user input."
)
_CODING_CONTINUE = (
    "[Fuse] The previous model response was empty after tool results. Continue the task: "
    "call tools if you still need evidence, otherwise write the user-facing answer now. "
    "Server-added notice, not user input."
)
_LENGTH_CONTINUE = (
    "[Fuse] The previous response hit the output token limit. Continue from exactly "
    "where the interrupted text above stops. Call tools if you still need them. "
    "Do not repeat text already delivered. Server-added notice, not user input."
)
_TOOL_CONTINUE = (
    "[Fuse] Your previous response was cut off in the middle of a tool call's JSON "
    "arguments (usually the output token limit), so that tool call was discarded and NOT "
    "executed. Do not continue the broken text mid-string. Re-issue it as one complete "
    "call whose arguments are a single valid JSON object. If the arguments were large "
    "(for example a whole file), the same call will be cut off again: split the work into "
    "smaller calls instead, such as writing a shorter first part and adding the rest with "
    "follow-up edits. Server-added notice, not user input."
)
_TRUNCATED_CALL_FAILURE = (
    "The model's tool call was cut off by its output limit {n} times in a row, so Fuse "
    "stopped instead of sending the same request again. Ask it to make the change in "
    "smaller pieces (for example, write the file in parts)."
)
_STATIONARITY_NUDGE = (
    "[Fuse] You have repeated the same tool calls several times with the same arguments. "
    "Stop repeating them. Either take a different action or write the final answer. "
    "Server-added notice, not user input."
)


async def summarize_conversation(request: Request, user, model: str, head: list[dict], instructions: str = "", *, tools=None, limit: int | None = None, cache_key: str | None = None) -> str:
    """Summarize with the configured cheap model, then the chat model.

    Either attempt failing degrades to the truncate strategy at the caller;
    a bad `context_summarizer_model` must not silently cost summary quality.

    `consume` is injected from this module so `patch("app.chat_runtime._consume_round")`
    keeps intercepting the provider round.
    """
    return await metadata.summarize_conversation(
        request, user, model, head, instructions,
        tools=tools, limit=limit, cache_key=cache_key, consume=_consume_round,
    )


async def generate_thread_title(request: Request, user, model: str, text: str, *, system: str = TITLE_SYSTEM, limit: int = chats.TITLE_SOURCE_MAX) -> str:
    return await _generate_metadata(request, user, model, text, system, limit, chats.parse_generated_title)


async def generate_recap(request: Request, user, model: str, text: str) -> str:
    return await _generate_metadata(request, user, model, text, RECAP_SYSTEM, 6000, chats.parse_generated_recap)


async def _generate_metadata(request, user, model, text, system, limit, parser):
    # `once` is resolved here, at call time, so
    # patch("app.chat_runtime._metadata_once") keeps binding.
    return await metadata.generate_metadata(
        request, user, model, text, system, limit, parser, once=_metadata_once
    )


async def _metadata_once(request, user, model, text, system, limit, parser):
    return await metadata.metadata_once(
        request, user, model, text, system, limit, parser, consume=_consume_round
    )


def _resume_delay(attempt_no: int, retry_after: float = 0.0) -> float:
    """Backoff lookup kept on this module so
    `patch("app.chat_runtime._RESUME_BACKOFF")` still applies."""
    return policy._resume_delay(attempt_no, retry_after, backoff=_RESUME_BACKOFF)


async def _run_tools(
    calls: list[dict],
    *,
    user_id: int,
    allowed_urls: set[str],
    request: Request,
    completion_i: int,
    budgets: dict,
    thread_id: int | None = None,
    bound_host_id: int | None = None,
    bound_cwd: str = "",
    coding: bool = False,
    loaded: list[str] | None = None,
    parent_run_id: str | None = None,
    user=None,
    session: dict | None = None,
    persist=None,
    diag: dict | None = None,
):
    """Tool-round entry kept on this module.

    `execute_tool` is resolved here, at call time, and injected explicitly, so
    `patch("app.chat_runtime.execute_tool")` keeps intercepting tool execution.
    """
    async with contextlib.aclosing(
        tool_runner.run_tools(
            calls,
            user_id=user_id,
            allowed_urls=allowed_urls,
            request=request,
            completion_i=completion_i,
            budgets=budgets,
            thread_id=thread_id,
            bound_host_id=bound_host_id,
            bound_cwd=bound_cwd,
            coding=coding,
            loaded=loaded,
            parent_run_id=parent_run_id,
            user=user,
            session=session,
            persist=persist,
            diag=diag,
            execute_tool=execute_tool,
        )
    ) as stream:
        async for item in stream:
            yield item


async def _touch_run_while(run_id, awaitable):
    """Keep updated_at fresh while work that yields no SSE (summarization) runs.

    The stall reaper kills a worker with no checkpoint for 180s; compaction
    can sit that long on a large transcript with no heartbeat otherwise.
    """
    if not run_id:
        return await awaitable

    async def _beats():
        from . import chat_store as store

        while True:
            await asyncio.sleep(15)
            try:
                store.touch_run(str(run_id))
            except Exception:
                pass

    task = asyncio.create_task(_beats())
    try:
        return await awaitable
    finally:
        task.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await task


def _compact_focus(instructions: str = "", *, is_child: bool = False, agent: dict | None = None) -> str:
    bits = []
    note = (instructions or "").strip()
    if note:
        bits.append(note)
    if is_child:
        label = str((agent or {}).get("description") or "").strip() if isinstance(agent, dict) else ""
        if label:
            bits.append("Subagent task label: " + label[:200])
        bits.append(
            "This is a subagent. Its parent's assignment is carried verbatim beside this handoff, so "
            "summarize progress against it rather than restating it. Keep exact paths, commands, exit "
            "codes, errors, and file findings the parent still needs for the final report."
        )
    return "\n\n".join(bits)


async def _compact_current(
    current_messages: list[dict],
    *,
    request: Request,
    user,
    model: str,
    type_id: str | None,
    trigger: str,
    thread_id,
    instructions: str = "",
    tools=None,
    override: int | None = None,
    is_child: bool = False,
    durable_messages: list[dict] | None = None,
    calibration: dict | None = None,
    run_id: str | None = None,
    agent: dict | None = None,
) -> tuple[list[dict], dict, str | None]:
    from . import chat_store as store

    cfg = context_meter.config()
    # Children share the parent's thread row; their compaction must stay in
    # their own run transcript (provider_messages), never mutate the parent
    # thread's compaction state, meter cache, or revision.
    persist_id = None if is_child else thread_id
    # Summarize the original transcript. Pruning first would replace tool
    # results with stubs, and the handoff would never see the evidence.
    system = [msg for msg in current_messages if isinstance(msg, dict) and msg.get("role") == "system"]
    source = [msg for msg in current_messages if isinstance(msg, dict) and msg.get("role") != "system"]
    snap = context_meter.meter_from_messages(
        current_messages, model=model, type_id=type_id, tools=tools, override=override, calibration=calibration
    )
    dropped, kept = source, []
    can_summarize = bool(dropped) and ctxmod.compactable_transcript(source, provider=True) and (
        trigger != "auto" or context_meter.should_compact(snap["used"], snap["limit"])
    )
    focus = _compact_focus(instructions, is_child=is_child, agent=agent)
    checkpoint = None
    if can_summarize:
        async def _sum(messages, instructions=""):
            return await summarize_conversation(
                request, user, model, [*system, *messages], instructions or focus, tools=tools, limit=snap["limit"],
                cache_key=f"run:{run_id}" if is_child and run_id else (f"thread:{thread_id}" if thread_id else None),
            )

        checkpoint = await _touch_run_while(
            run_id,
            ctxmod.build_checkpoint(
                source,
                strategy=cfg.strategy,
                instructions=focus,
                trigger=trigger,
                summarize=_sum,
                tokens_before=snap["used"],
                provider=True,
                keep_tail=cfg.keep_tail,
                head=dropped,
                tail=kept,
            ),
        )
    if checkpoint:
        # The child's task arrived as its first user row, which is now in the
        # dropped span; the summary alone would paraphrase scope and report format.
        pinned = ctxmod.pinned_assignment(dropped, kept) if is_child else ""
        rebuilt = [
            *system,
            *ctxmod.continuation(checkpoint["summary"], kept, pinned=pinned),
            *ctxmod.retained_skill_rows(source),
        ]
        after = context_meter.meter_from_messages(rebuilt, model=model, type_id=type_id, tools=tools, override=override, calibration=calibration)
        after["estimated"] = True
        after["standing"] = True
        after["scope"] = "transcript"
        if persist_id:
            try:
                store.apply_compaction_checkpoint(
                    user["id"],
                    persist_id,
                    # A boot compact on a fresh turn summarizes exactly the
                    # durable rows; anchor the projection so later turns reuse
                    # this summary instead of re-summarizing every turn.
                    ctxmod.durable_checkpoint(durable_messages, checkpoint) or checkpoint,
                    meter=after,
                )
            except Exception:
                logger.exception("Could not persist compaction checkpoint")
        return rebuilt, after, checkpoint.get("strategy") or cfg.strategy

    # Not compactable. Never stub tool results that were already sent: that
    # rewrites the cached prefix and loses the evidence, and the next request
    # would re-send the full bodies anyway. Shrinking history is compaction's
    # job; a non-compactable transcript is returned untouched.
    snap = context_meter.meter_from_messages(
        current_messages, model=model, type_id=type_id, tools=tools, override=override, calibration=calibration
    )
    return current_messages, snap, None


async def stream_dashboard_chat(request: Request, user, raw: dict, *, frozen_context: dict | None = None) -> StreamingResponse:
    from . import chat_history
    if frozen_context is not None:
        # Server-owned worker state, never a JSON or HTTP-header override.
        request.scope["fuse_retained_history"] = True
    if not isinstance(raw, dict):
        raise HTTPException(400, "Invalid JSON")
    model = raw.get("model") or ""
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise HTTPException(400, "Invalid model")
    agent = raw.get("agent") if isinstance(raw.get("agent"), dict) else {}
    if agent.get("skip_sanitize") or (frozen_context is not None and raw.get("history_replay")):
        messages = raw.get("messages") if isinstance(raw.get("messages"), list) else []
        if not messages:
            raise HTTPException(400, "Invalid messages")
    else:
        messages = sanitize_messages(raw.get("messages"))
    messages = [{k: v for k, v in m.items() if k != "reasoning_content" or m.get("_fuse_reasoning_model") == model}
                for m in messages]
    tools = parse_tool_names(raw.get("tools"))
    # Only the server-owned run scheduler supplies this keyword, never JSON overrides.
    prefs = frozen_context if frozen_context is not None else chats.get_user_context(user["id"])
    thread_context = ""
    thread = None
    thread_id = raw.get("thread_id")
    if thread_id is not None:
        try:
            thread_id = int(thread_id)
        except (TypeError, ValueError):
            thread_id = None
        thread = chats.get_thread(user["id"], thread_id) if thread_id else None
        if thread:
            thread_context = thread.get("context") or ""
            if "tools" not in raw:
                tools = parse_tool_names(thread.get("tools"))
    if isinstance(raw.get("thread_context"), str):
        thread_context = _clip(raw.get("thread_context"), chats.CONTEXT_MAX)
    from . import hosts as hosts_mod

    settings_row = hosts_mod.settings_for(user["id"])
    project_session = bool(agent.get("project_session")) or bool(thread and thread.get("kind") == "project")
    if agent.get("scheduled"):
        coding = bool(agent.get("coding"))
    else:
        coding = bool(agent.get("coding")) or bool(settings_row.get("agent_coding")) or project_session
    bound_host_id = agent.get("bound_host_id") if agent.get("bound_host_id") is not None else (thread.get("host_id") if thread else None)
    bound_cwd = str(agent.get("bound_cwd") or "")
    if not bound_cwd and thread and thread.get("project_id"):
        proj = hosts_mod.resolve_project_id(user["id"], thread.get("project_id"))
        if proj:
            bound_cwd = proj.get("remote_path") or ""
    nested = bool(agent.get("allow_nested_spawn") if agent else settings_row.get("allow_nested_spawn"))
    depth = int(agent.get("depth") or 0)
    max_depth = 3 if nested else 1
    kind = str(agent.get("subagent_type") or "")
    # explore/plan children can never spawn (agents.spawn forces their nested
    # flag off); offering the tool only produces certain SpawnCapError failures.
    allow_spawn = coding and depth < max_depth and kind not in ("explore", "plan")
    parent_run_id = agent.get("run_id") if isinstance(agent.get("run_id"), str) else None
    run_id = raw.get("run_id") if isinstance(raw.get("run_id"), str) else parent_run_id
    from . import skills as skills_mod

    is_child = bool(agent.get("parent_run_id") or depth)
    # Children are bounded tasks: scope the machine catalog to their bound host and
    # skip the skills index (a parent that wants a skill used says so in the prompt).
    host_catalog = (
        hosts_mod.catalog_for_prompt(
            user["id"],
            bound_host_id=bound_host_id or None,
            bound_cwd=bound_cwd or "",
            only_bound=is_child,
        )
        if coding
        else ""
    )
    skills_index = skills_mod.catalog_for_prompt(user["id"]) if coding and not is_child else ""
    bound_label = ""
    if coding and bound_host_id:
        bound_row = hosts_mod.get_host(user["id"], bound_host_id)
        if bound_row:
            alias = bound_row.get("alias") or ""
            cwd = bound_cwd or bound_row.get("default_cwd") or ""
            bound_label = f"{alias} cwd={cwd}" if cwd else alias
    from .vault import unlocked as vault_unlocked_for

    vault_ok = True
    if coding:
        if "vault_unlocked" in agent:
            vault_ok = bool(agent.get("vault_unlocked"))
        else:
            vault_ok = vault_unlocked_for(user["id"])
    from .agent_catalog import sanitize_loaded

    loaded_tools: list[str] = sanitize_loaded(agent.get("loaded_tools") if isinstance(agent.get("loaded_tools"), list) else [])
    if coding:
        from .agent_catalog import ARTIFACT_NAMES, WORKSPACE_NAMES

        loaded_tools = sanitize_loaded([*WORKSPACE_NAMES, *ARTIFACT_NAMES, *loaded_tools,
                                        *(["propose_skill"] if not is_child and not agent.get("auto") and not agent.get("scheduled") else [])])
    if kind == "explore":
        from .agent_catalog import EXPLORE_TOOLS

        loaded_tools[:] = [n for n in loaded_tools if n in EXPLORE_TOOLS]
    elif kind == "plan":
        from .agent_catalog import PLAN_TOOLS

        loaded_tools[:] = [n for n in loaded_tools if n in PLAN_TOOLS]
    session = {
        "coding": coding,
        "project_session": project_session,
        "bound_host_id": bound_host_id,
        "bound_cwd": bound_cwd,
        "enabled_tools": list(tools),
        "subagent_type": kind,
        "allow_spawn": allow_spawn,
        "depth": depth,
        "run_id": str(agent.get("run_id") or run_id or ""),
        "scheduled": bool(agent.get("scheduled")),
        "auto": bool(agent.get("auto")),
        "requester": (
            {"role": "subagent", "type": kind, "description": str(agent.get("description") or "")[:200]}
            if is_child
            else {"role": "main"}
        ),
        "unattended": bool(agent.get("auto") or agent.get("scheduled")),
        "allow_skill_author": bool(
            coding
            and not is_child
            and not agent.get("scheduled")
            and not agent.get("auto")
        ),
    }
    completion_limit = MAX_COMPLETIONS
    if coding:
        # Children and parents both run until they write a final answer,
        # stall, compact, or hit the wall clock. max_completions 0 / omitted /
        # the legacy 16–24 default means unlimited.
        completion_limit = _coding_completion_limit(agent)
    project_agents = ""
    project_agents_name = ""
    # Connector (fuse-agent) hosts need no vault; SSH hosts fail gracefully inside
    # the loader (VaultLocked -> short negative cache), so always attempt the load.
    if project_session and bound_host_id and bound_cwd:
        try:
            from . import workspace as workspace_mod

            project_agents_name, project_agents = await asyncio.wait_for(
                workspace_mod.load_project_agents(user["id"], bound_host_id, bound_cwd),
                timeout=4.0,
            )
        except Exception:
            # A slow host must not blank a block the provider already cached:
            # serve the last good body until a read confirms otherwise.
            project_agents_name, project_agents = workspace_mod.last_project_agents(
                user["id"], bound_host_id, bound_cwd
            )
    ssh_any = False
    if coding:
        ssh_any = any(
            (h.get("auth_type") or "") != "agent" and not h.get("disabled")
            for h in hosts_mod.list_bundle(user["id"])["hosts"]
        )
    candidate_system = build_system_prompt(
        instructions=prefs["instructions"],
        profile=prefs["profile"],
        thread_context=thread_context,
        tools=tools,
        host_catalog=host_catalog,
        skills_index=skills_index,
        coding=coding,
        project_session=project_session,
        bound_label=bound_label,
        project_agents=project_agents,
        project_agents_name=project_agents_name,
        loaded_tools=loaded_tools,
        spawn_tools=allow_spawn,
        subagent=is_child,
        subagent_type=kind,
    )
    snapshot = raw.get("prompt_snapshot") if frozen_context is not None else None
    prompt_snapshot = copy.deepcopy(snapshot) if isinstance(snapshot, dict) and isinstance(snapshot.get("system"), str) else {}
    system = prompt_snapshot.get("system", candidate_system)
    model_messages = messages if agent.get("resumed") or agent.get("followup_continuation") else expand_slash_messages(
        messages, project_session=project_session, bound_cwd=bound_cwd)
    work = [{"role": "system", "content": system}, *model_messages]
    if not agent.get("resumed") and not agent.get("followup_continuation"):
        work = _inject_session_tail(
            work,
            _session_tail_block(
                _artifact_lines(artifacts.list_for_prompt(user["id"], thread_id)),
                vault_locked=bool(coding and ssh_any and not vault_ok),
            ),
        )
    payload = {"model": model.strip(), "messages": work, "stream": True}
    if "prompt_cache_key" not in payload:
        # One cache identity for the life of the conversation: the thread for
        # parents, the child's own run for subagents (their history differs,
        # and siblings sharing the thread key would evict each other's pin).
        if is_child and (run_id or parent_run_id):
            payload["prompt_cache_key"] = f"run:{run_id or parent_run_id}"
        elif thread_id is not None:
            payload["prompt_cache_key"] = f"thread:{thread_id}"
    effort = raw.get("reasoning_effort")
    if effort is not None:
        if not isinstance(effort, str) or effort not in api._REASONING_EFFORTS:
            raise HTTPException(400, "Invalid reasoning_effort")
        payload["reasoning_effort"] = effort
    if raw.get("max_tokens") is not None:
        try:
            value = raw["max_tokens"]
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
            if not math.isfinite(number) or not number.is_integer():
                raise ValueError
            payload["max_tokens"] = max(1, min(int(number), settings.max_tokens))
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, "Invalid max_tokens") from None
    candidate_schemas = openai_tools(
        tools,
        coding=coding,
        extra=loaded_tools,
        allow_spawn=allow_spawn,
        project_session=project_session,
    )
    prompt_context = {"instructions": prefs["instructions"], "profile": prefs["profile"],
                      "thread_context": thread_context}
    schemas = copy.deepcopy(prompt_snapshot.get("tools", candidate_schemas))
    prompt_refreshed = False
    if not (agent.get("resumed") or agent.get("followup_continuation")) and (
        schemas != candidate_schemas or prompt_snapshot.get("context") != prompt_context
    ):
        prompt_refreshed = "system" in prompt_snapshot
        # Explicit settings changes take effect on the next turn. Resumes keep
        # the exact prefix; incidental catalog changes wait until compaction.
        schemas = copy.deepcopy(candidate_schemas)
        system = candidate_system
        work[0] = {"role": "system", "content": system}
        prompt_snapshot.update(system=system, tools=copy.deepcopy(schemas), context=prompt_context)
    if "system" not in prompt_snapshot:
        prompt_snapshot = {"system": system, "tools": copy.deepcopy(schemas), "context": prompt_context}
    if schemas:
        payload["tools"] = schemas
        payload["tool_choice"] = "auto"
        payload["parallel_tool_calls"] = True

    async def gen():
        nonlocal completion_limit
        allowed_urls: set[str] = set(agent.get("allowed_urls") or []) | _user_public_urls(messages)
        total_usage: dict = _add_usage({}, agent.get("usage_before"))
        dropped_tools = False
        tools_ran = any(m.get("role") == "tool" and not _is_preload_tool(m) for m in work)
        current = dict(payload)
        current_messages = list(work)
        budgets = {
            "search_queries": int(agent.get("search_queries") or 0),
            "fetches": int(agent.get("fetches") or 0),
            "fetch_attempts": int(agent.get("fetch_attempts") or 0),
        }
        command_delivery_ids = []
        src_type = None
        # take_child_results/take_followups/take_steers are non-destructive
        # peeks; they only clear once the transcript persists. A failed
        # checkpoint would otherwise re-inject the same envelope next round.
        # In-memory dedupe closes that; a real crash still redelivers via the
        # stored transcript / wake path.
        delivered_local: set[str] = set()
        start_i = _completion_start_index(agent, messages, completion_limit)

        def refresh_prompt():
            nonlocal current_messages, prompt_snapshot, cache_prior
            if transcript and transcript[0].get("role") == "system":
                transcript[0] = {"role": "system", "content": candidate_system}
            else:
                transcript.insert(0, {"role": "system", "content": candidate_system})
            current_messages = transcript
            prompt_snapshot = {"system": candidate_system, "tools": copy.deepcopy(candidate_schemas), "context": prompt_context}
            if candidate_schemas:
                current["tools"] = copy.deepcopy(candidate_schemas)
            else:
                current.pop("tools", None)
            anchor.update(prompt=0, sent=None)
            # A rebuilt prompt is a new prefix; its first request may miss.
            cache_prior = None

        async def persist_jobs(jobs=None):
            nonlocal current_messages
            current_messages = transcript
            if not run_id:
                return
            try:
                from . import chat_store as store
                from .async_work import durable_call

                extra = {
                    "allowed_urls": list(allowed_urls),
                    "search_queries": budgets.get("search_queries", 0),
                    "fetches": budgets.get("fetches", 0),
                    "fetch_attempts": budgets.get("fetch_attempts", 0),
                    "command_delivery_ids": list(command_delivery_ids),
                    "prompt_snapshot": prompt_snapshot,
                }
                await durable_call(store.save_turn_progress,
                    run_id,
                    _provider_snapshot(current_messages, jobs,
                        content=str((summary or {}).get("content") or "") if jobs else "",
                        reasoning=(summary or {}).get("reasoning") if jobs else "", model=model,
                        thinking=(summary or {}).get("thinking") if jobs else None),
                    loaded_tools=list(loaded_tools),
                    extra=extra,
                )
            except Exception:
                logger.exception("Chat transcript checkpoint failed for %s", run_id)
                raise

        # One replayable transcript, extended only by new exchanges or replaced
        # wholesale by an explicit compaction checkpoint.
        transcript = current_messages

        invoke = agent.get("invoke_skill") if isinstance(agent.get("invoke_skill"), dict) else None
        if coding and invoke:
            transcript, preload_event = apply_skill_preload(
                current_messages,
                user_id=user["id"],
                run_id=str(run_id or ""),
                invoke=invoke,
            )
            if preload_event:
                await persist_jobs()
                yield _sse(preload_event)

        src_type_guess = _model_type_guess(model)
        last_round_usage: dict = {}
        summary: dict | None = None
        compacted_this_turn = False
        stored_meter = agent.get("meter") if is_child and isinstance(agent.get("meter"), dict) else None
        if not stored_meter:
            stored_meter = ((thread or {}).get("compaction") or {}).get("meter") if isinstance(thread, dict) else None
        src_type_guess = context_meter.source_type_for_model(model, stored_meter)
        resolved_window = int((stored_meter or {}).get("limit") or 0) if (stored_meter or {}).get("model") == model else None
        if src_type_guess == "explabs":
            from .sources.explabs import cached_catalog

            resolved_window = context_meter.window_for(model, "explabs", catalog=cached_catalog(user["id"]))
        calibration = (stored_meter or {}).get("calibration")
        if calibration and calibration.get("model") != model:
            calibration = None
        prior_used = int((stored_meter or {}).get("used") or 0) if isinstance(stored_meter, dict) else 0
        window_override = None
        if isinstance(thread, dict):
            raw_override = ((thread.get("compaction") or {}).get("window_override")) or 0
            try:
                window_override = int(raw_override) or None
            except (TypeError, ValueError):
                window_override = None

        # The newest request's prompt-cache probe, carried across turns on the
        # thread; one miss notice per turn at most (chat_generation.cache_watch).
        cache_prior = None
        cache_warned = False
        if not is_child and thread_id:
            try:
                from .chat_checkpoint import load_compaction

                cache_prior = (load_compaction(user["id"], thread_id) or {}).get("cache")
            except Exception:
                logger.exception("Could not load the prompt-cache probe")

        # Last provider prompt_tokens, and the projected messages that prompt covered.
        # Later readings add only the suffix. A rebuilt transcript clears this.
        anchor = {"prompt": 0, "sent": None}

        def _note_anchor(usage, sent_messages):
            prompt = context_meter.prompt_token_count(usage)
            if not prompt:
                return
            # Devin reports billing/cache deltas, not an inclusive prompt size;
            # anchoring on a delta produces a meaningless, oscillating meter.
            if (src_type or src_type_guess) == "devin":
                return
            # sent_messages is already for_model-projected; projecting again
            # strips reasoning_content (the _fuse_reasoning_model marker is
            # gone after pass one) and the anchor never prefix-matches.
            sent = list(sent_messages)
            anchor["prompt"] = prompt
            anchor["sent"] = sent

        def _anchored(projected):
            sent = anchor["sent"]
            if not anchor["prompt"] or not isinstance(sent, list):
                return None
            if len(projected) < len(sent) or projected[:len(sent)] != sent:
                anchor["prompt"] = 0
                anchor["sent"] = None
                return None
            return projected[len(sent):]

        def _live_meter(extra_tokens: int = 0, estimated: bool | None = None, usage=None, response=None):
            measured = current_messages
            if response:
                assistant = {"role": "assistant", "content": response.get("content") or ""}
                if response.get("calls"):
                    assistant["tool_calls"] = _tool_messages(jobs_from_calls(response["calls"], 0))[0]["tool_calls"]
                assistant.update(_native_reasoning(response.get("reasoning"), model, response.get("thinking")))
                measured = [*current_messages, assistant]
            projected = chat_history.for_model(measured, model)
            added = _anchored(projected)
            if added is not None:
                snap = context_meter.next_request_meter(
                    projected,
                    model=model,
                    type_id=src_type or src_type_guess,
                    tools=current.get("tools"),
                    override=window_override or resolved_window,
                    anchor_prompt=anchor["prompt"],
                    added=added,
                    calibration=calibration,
                )
                if extra_tokens:
                    snap["used"] = snap["used"] + max(0, extra_tokens)
                    snap["estimated"] = True
                    snap["parts"] = context_meter._fit_parts(snap.get("parts"), snap["used"])
            else:
                snap = context_meter.meter_from_messages(
                    projected,
                    model=model,
                    type_id=src_type or src_type_guess,
                    extra_tokens=extra_tokens,
                    tools=current.get("tools"),
                    override=window_override or resolved_window,
                    calibration=calibration,
                )
            # Usage is a diagnostic of the last request, never the displayed total.
            # The meter reports retained context for the NEXT request, so the
            # measured prompt is anchored and only the replayable suffix is
            # added. prompt+completion would count billed output that never
            # replays (truncated calls, provider-dropped reasoning).
            if usage:
                snap["provider_usage"] = dict(usage)
            if calibration and "calibration" not in snap:
                snap["calibration"] = dict(calibration)
            if estimated is not None:
                snap["estimated"] = estimated
            snap["scope"] = "prompt"
            snap["run_id"] = run_id
            snap["source_type"] = src_type or src_type_guess
            snap["history_info"] = raw.get("history_info") or {"mode": "legacy" if run_id and not raw.get("history_replay") and not is_child else "full"}
            if snap.get("parts") and snap["parts"].get("total") != snap.get("used"):
                snap["parts"] = context_meter._fit_parts(snap["parts"], snap["used"])
            return snap

        def _persist_meter(snap: dict):
            if is_child and run_id:
                try:
                    from . import chat_store as store

                    store.save_child_meter(str(run_id), {**snap, "scope": "prompt", "run_id": run_id})
                except Exception:
                    logger.exception("Could not persist child context meter")
                return
            if not thread_id:
                return
            try:
                from . import chat_store as store

                store.save_meter(user["id"], thread_id, {**snap, "scope": "prompt", "run_id": run_id})
            except Exception:
                logger.exception("Could not persist context meter")

        boot = _live_meter(estimated=True)
        yield _sse(context_meter.context_event(boot, phase="start"))
        _persist_meter(boot)
        if context_meter.should_auto_compact(
            # Boot measures the exact payload about to be sent, not a
            # transient mid-turn estimate; the spike guard does not apply.
            boot["used"], boot["limit"], estimated=False, prior_used=prior_used
        ):
            yield _sse(context_meter.context_event({**boot, "compacting": True}, phase="compacting"))
            transcript, boot, kind = await _compact_current(
                current_messages,
                request=request,
                user=user,
                model=model,
                type_id=src_type_guess,
                trigger="auto",
                thread_id=thread_id,
                tools=current.get("tools"),
                override=boot["limit"],
                is_child=is_child,
                # A fresh boot transcript mirrors the durable rows, so its
                # summary can anchor the durable projection. A resumed run's
                # transcript is a restored provider snapshot and cannot.
                durable_messages=(thread or {}).get("messages") if not agent.get("resumed") and not raw.get("history_replay") else None,
                calibration=calibration,
                run_id=run_id,
                agent=agent,
            )
            real = kind in ("summarize", "truncate")
            compacted_this_turn = real
            boot["standing"] = True
            if real:
                refresh_prompt()
                last_round_usage = {}
                current["messages"] = transcript
                yield _sse(context_meter.context_event({**boot, "compacting": False}, phase="compacted"))
                yield _sse(ctxmod.compact_handoff_event(ctxmod.prior_handoff(current_messages)))
            else:
                yield _sse(context_meter.context_event(boot, phase="update"))
            _persist_meter(boot)
        await persist_jobs()
        completion_i = start_i
        resumes_left = MAX_RESUME_RETRIES
        stall_fails = 0
        idle_stalls = 0
        length_salvages = 0
        empty_retries = 0
        same_calls = 0
        last_call_sig: tuple | None = None
        extra_wrap = False
        last_compact_used = None
        while True:
            from . import chat_store as store
            current_messages = transcript

            if not (is_child or completion_i < completion_limit or extra_wrap):
                if run_id and not store.close_steer_window(str(run_id)):
                    extra_wrap = True
                else:
                    break

            if run_id:
                # One drain for everything that arrived while this run worked
                # (chat_generation.inbox): background results, user steers on
                # a root, parent follow-ups on a subagent.
                box = inbox.drain(
                    store, run_id=str(run_id), is_child=is_child, user_id=user["id"],
                    thread_id=thread_id, transcript=current_messages, delivered_local=delivered_local,
                )
                command_delivery_ids.extend(box.command_ids)
                if box.results:
                    # Delivery receipts commit with the retained transcript.
                    transcript.append(inbox.results_message(box.results))
                    await persist_jobs()
                    command_delivery_ids = []
                    # The user sees the hand-off where it happened: the run
                    # worker records a delivery segment at this point of the
                    # reply, rendered like a wake turn's result rows.
                    yield _sse({"fuse": {"event": "delivery", "text": inbox.display_text(box.results)}})
                if box.steers:
                    # User steers are real chat messages (already visible in
                    # the thread); they land here at the next tool boundary.
                    for item in box.steers:
                        transcript.append(store.steer_message(item))
                    await persist_jobs()
                    # A new user instruction gets a real response budget, not
                    # the exhausted turn's text-only wrap-up round.
                    completion_limit = max(completion_limit, completion_i + MAX_COMPLETIONS)
                    extra_wrap = False
                    stall_fails = same_calls = length_salvages = empty_retries = 0
                    last_call_sig = None
                    budgets.update(search_queries=0, fetches=0, fetch_attempts=0)
                if not is_child:
                    delivered_ids = list(dict.fromkeys([item["id"] for item in box.steers] + store.steer_display_deliveries(str(run_id))))
                    if delivered_ids:
                        # The worker flushes all preceding tokens before sealing
                        # the visible split; only the next response belongs below it.
                        yield _sse({"fuse": {"event": "steer.delivered", "ids": delivered_ids}})
                else:
                    if box.followups:
                        transcript.append(inbox.followup_message(box.followups))
                        await persist_jobs()
                    delivered = [
                        match
                        for text in box.followups
                        for match in re.findall(r"\(followup:([0-9a-f]{1,32})\)", text)
                    ]
                    # Recovery after a crash or a lost frame: envelopes durable in
                    # provider_messages without a display mark re-emit here, so a
                    # follow-up never permanently renders above its whole leg.
                    delivered += store.followup_display_deliveries(str(run_id))
                    if delivered:
                        # The worker marks where in the live leg these landed, so
                        # the panel shows them there instead of above the whole leg.
                        yield _sse({"fuse": {"event": "followup.delivered", "ids": list(dict.fromkeys(delivered))}})
            # Coding parents are never force-stopped for "last round". Search chats
            # wrap up at MAX_COMPLETIONS; extra_wrap is one text-only round after
            # last-round tools actually ran. dropped_tools always forces text.
            force = dropped_tools or (
                not is_child and not coding and (extra_wrap or completion_i >= completion_limit - 1)
            )
            retried_parallel = False
            retried_none = False
            result = None
            # Steers, background results and newly loaded tool schemas can
            # grow context after the preceding tool-boundary check.
            preflight = _live_meter()
            if (context_meter.should_auto_compact(
                    preflight["used"], preflight["limit"],
                    estimated=False, prior_used=prior_used)
                    and last_compact_used is None):
                yield _sse(context_meter.context_event({**preflight, "compacting": True}, phase="compacting"))
                before_compact_used = preflight["used"]
                transcript, preflight, kind = await _compact_current(
                    transcript, request=request, user=user, model=model,
                    type_id=src_type or src_type_guess, trigger="auto", thread_id=thread_id,
                    tools=current.get("tools"), override=preflight["limit"], is_child=is_child,
                    calibration=calibration, run_id=run_id, agent=agent)
                if kind in ("summarize", "truncate"):
                    refresh_prompt()
                    last_compact_used = preflight["used"] if preflight["used"] >= int(before_compact_used * 0.95) else None
                    yield _sse(ctxmod.compact_handoff_event(ctxmod.prior_handoff(current_messages)))
                yield _sse(context_meter.context_event(preflight, phase="compacted" if kind in ("summarize", "truncate") else "update"))
                _persist_meter(preflight)
                await persist_jobs()
            _user = user
            src = None
            upstream = ""
            requested = ""
            while True:
                if force and (not transcript or transcript[-1] != {"role": "user", "content": _NUDGE}):
                    # Durable nudge row: the sent request and the retained
                    # history stay the same bytes; retries dedupe the tail.
                    transcript.append({"role": "user", "content": _NUDGE})
                    current_messages = transcript
                    await persist_jobs()
                send = dict(current)
                send["messages"] = current_messages
                send["model"] = model.strip()
                if force:
                    if send.get("tools") and src_type != "ollama" and not retried_none:
                        send["tool_choice"] = "none"
                    else:
                        send.pop("tool_choice", None)
                if src_type == "ollama":
                    send.pop("parallel_tool_calls", None)
                    send.pop("tool_choice", None)
                yield _sse({"fuse": {"event": "activity", "phase": "model", "run_id": run_id}})
                send["messages"] = chat_history.for_model(send["messages"], model)
                request_meter = context_meter.meter_from_messages(send["messages"], model=model,
                    type_id=src_type or src_type_guess, tools=send.get("tools"), override=window_override or resolved_window)
                request_estimate = request_meter["used"]
                added = _anchored(send["messages"])
                if added is not None:
                    request_meter = context_meter.next_request_meter(
                        send["messages"], model=model, type_id=src_type or src_type_guess,
                        tools=send.get("tools"), override=window_override or resolved_window,
                        anchor_prompt=anchor["prompt"], added=added, calibration=calibration,
                    )
                else:
                    request_meter = context_meter.calibrate_meter(request_meter, calibration)
                request_meter.update(scope="prompt", run_id=run_id, source_type=src_type or src_type_guess)
                yield _sse(context_meter.context_event(request_meter, phase="request"))
                _persist_meter(request_meter)
                try:
                    _user = src = upstream = requested = result = None
                    # `invoked`, not `payload`: the loop variable must not
                    # shadow the request payload gen() was built from.
                    async for kind, invoked in _with_beats(api.invoke_chat(
                        request, user, send, image_snapshot=prompt_snapshot.setdefault("images", {}),
                        prepared=persist_jobs,
                    )):
                        if kind == "beat":
                            yield _sse({"fuse": {"event": "heartbeat"}})
                            continue
                        if kind == "err":
                            raise invoked
                        _user, src, upstream, requested, result = invoked
                except HTTPException as exc:
                    if _is_idle_timeout(str(exc.detail)):
                        idle_stalls += 1
                    if (
                        _should_resume(exc.status_code, str(exc.detail), resumes_left=resumes_left, idle_stalls=idle_stalls)
                        and not await request.is_disconnected()
                    ):
                        resumes_left -= 1
                        attempt_no = MAX_RESUME_RETRIES - resumes_left
                        delay = _resume_delay(attempt_no)
                        yield _sse(
                            {
                                "fuse": {
                                    "event": "notice",
                                    "message": (
                                        f"Provider error ({exc.status_code}) — retrying this step in "
                                        f"{int(delay)}s (attempt {attempt_no} of {MAX_RESUME_RETRIES})."
                                    ),
                                }
                            }
                        )
                        async for frame in _heartbeat_sleep(delay):
                            yield frame
                        continue
                    yield _sse(
                        _exhausted_provider_error(
                            is_child,
                            {"error": {"message": str(exc.detail), "code": exc.status_code}},
                            exc.status_code,
                            attempts=MAX_RESUME_RETRIES - resumes_left,
                        )
                    )
                    _snap = _live_meter(); yield _sse(context_meter.context_event(_snap, phase="end")); _persist_meter(_snap)
                    yield b"data: [DONE]\n\n"
                    return
                shed = (getattr(result, "response_headers", None) or {}).get("X-Fuse-Images-Shed")
                if shed:
                    yield _sse({"fuse": {"event": "notice", "message": (
                        f"The provider refused this many images, so {shed} older image(s) were left out "
                        "of this request. They remain in the chat.")}})
                src_type = src.type_id
                if not (src_type in ("llama", "ollama", "openai") and src_type == src_type_guess
                        and resolved_window and not context_meter.cached_window(src_type, upstream or model)):
                    catalog = None
                    if src_type == "explabs":
                        from .sources.explabs import cached_catalog

                        catalog = cached_catalog(user["id"])
                    resolved_window = context_meter.window_for(upstream or model, src_type, catalog=catalog)
                if not window_override and src_type != "explabs":
                    context_meter.remember_window(src_type, model, resolved_window)
                msg = api._error_message(result.body) if result.status != 200 else ""
                if result.status != 400:
                    break
                if (
                    not retried_parallel
                    and send.get("parallel_tool_calls") is not None
                    and re.search(r"parallel_tool", msg or "", re.I)
                ):
                    current.pop("parallel_tool_calls", None)
                    retried_parallel = True
                    continue
                if (
                    force
                    and not dropped_tools
                    and not retried_none
                    and "tool_choice" in send
                    and re.search(r"tool_choice", msg or "", re.I)
                ):
                    current.pop("tool_choice", None)
                    retried_none = True
                    continue
                first_tools_request = (
                    completion_i == 0
                    and not dropped_tools
                    and not any(m.get("role") == "tool" and not _is_preload_tool(m) for m in current_messages)
                    and schemas
                )
                if first_tools_request and _TOOLS_UNSUPPORTED.search(msg or ""):
                    # One-shot compatibility path. Folding tools into text changes
                    # the prefix, so it stays on this request's copy only.
                    api._safe_record_usage(
                        _user["id"], src.source_id, result.credential_id, upstream,
                        result.usage, key_id=_user.get("key_id"), provider=src.type_id,
                        requested_model=requested, request_status="failed", count_request=False,
                    )
                    if src.type_id not in POOL_TYPES:
                        _cap_set(src.type_id, upstream, "no_tools")
                    dropped_tools = True
                    current_messages = fold_preload_skill(current_messages)
                    current, current_messages = _drop_tools(current, current_messages)
                    if "web_search" in tools:
                        packed = ""
                        async with contextlib.aclosing(_retrieval_first(request, user["id"], current_messages, budgets)) as retrieval:
                            async for item in retrieval:
                                if isinstance(item, tuple) and item and item[0] == "packed":
                                    packed = item[1] or ""
                                elif isinstance(item, dict):
                                    yield _sse({"fuse": item})
                        if packed:
                            current_messages = inject_web_context(current_messages, packed)
                    else:
                        yield _sse(
                            {
                                "fuse": {
                                    "event": "notice",
                                    "message": "This model does not accept tools. Artifacts are unavailable for this reply.",
                                }
                            }
                        )
                    transcript = current_messages
                    prompt_snapshot["tools"] = []
                    await persist_jobs()
                    force = True
                    continue
                if force:
                    yield _sse(
                        {
                            "fuse": {
                                "event": "notice",
                                "message": "This model rejected the final-answer request.",
                            }
                        }
                    )
                    yield _sse(_error_obj(result.status, result.body))
                    _snap = _live_meter(); yield _sse(context_meter.context_event(_snap, phase="end")); _persist_meter(_snap)
                    yield b"data: [DONE]\n\n"
                    return
                break

            if result.status != 200:
                api._safe_record_usage(
                    _user["id"], src.source_id, result.credential_id, upstream,
                    result.usage, key_id=_user.get("key_id"), provider=src.type_id,
                    requested_model=requested, request_status="failed", count_request=True,
                )
                # Transient provider failure mid-turn: re-run this same round
                # (same completion budget) with the context accumulated so far.
                err_msg = api._error_message(result.body)
                if _is_idle_timeout(err_msg):
                    idle_stalls += 1
                if (
                    _should_resume(result.status, err_msg, resumes_left=resumes_left, idle_stalls=idle_stalls)
                    and not await request.is_disconnected()
                ):
                    resumes_left -= 1
                    attempt_no = MAX_RESUME_RETRIES - resumes_left
                    delay = _resume_delay(attempt_no, _header_retry_after(getattr(result, "response_headers", None)))
                    yield _sse(
                        {
                            "fuse": {
                                "event": "notice",
                                "message": (
                                    f"Provider error ({result.status}) — retrying this step in "
                                    f"{int(delay)}s (attempt {attempt_no} of {MAX_RESUME_RETRIES})."
                                ),
                            }
                        }
                    )
                    async for frame in _heartbeat_sleep(delay):
                        yield frame
                    continue
                yield _sse(_exhausted_provider_error(is_child, result.body, result.status, attempts=MAX_RESUME_RETRIES - resumes_left))
                _snap = _live_meter(); yield _sse(context_meter.context_event(_snap, phase="end")); _persist_meter(_snap)
                yield b"data: [DONE]\n\n"
                return
            diag = {
                "run_id": str(run_id or ""),
                "model": model.strip(),
                "source_type": src_type or src_type_guess or "",
                "upstream_model": upstream or "",
                "round": completion_i,
                "user_text": _latest_user_text(messages),
            }
            summary = None
            round_client_usage = {}
            # Content the client already saw this round; on an exception the
            # consume summary is gone, so the resume hand-off replays it from
            # here instead of restarting the round from scratch.
            round_partial = ""
            try:
                async with contextlib.aclosing(
                    _consume_round(result, _user, src, upstream, requested, coding=coding)
                ) as provider:
                    async for kind, payload_obj in provider:
                        if kind == "out":
                            if (payload_obj.get("fuse") or {}).get("event") == "activity":
                                payload_obj = {"fuse": {**payload_obj["fuse"], "run_id": run_id}}
                            if payload_obj.get("usage"):
                                reported = usage_from_obj(payload_obj, reasoning_separate=(src_type == "grok"))
                                if reported:
                                    round_client_usage = merge_usage(round_client_usage, reported) if round_client_usage else reported
                                    payload_obj = {**payload_obj, "usage": _add_usage(total_usage, round_client_usage)}
                            choice = (payload_obj.get("choices") or [{}])[0] if isinstance(payload_obj.get("choices"), list) else {}
                            delta = choice.get("delta") if isinstance(choice, dict) and isinstance(choice.get("delta"), dict) else {}
                            round_partial += str(delta.get("content") or "")
                            yield _sse(payload_obj)
                        elif kind == "beat":
                            # Same liveness event tool-wait heartbeats already
                            # emit; keeps the run row and UI clock moving while
                            # the model thinks in silence.
                            yield _sse({"fuse": {"event": "heartbeat"}})
                        else:
                            summary = payload_obj
            except Exception as exc:
                logger.warning("Dashboard upstream stream failed: %s", type(exc).__name__)
                err_msg = f"upstream stream failed ({type(exc).__name__})"
                if _is_idle_timeout(err_msg):
                    idle_stalls += 1
                if (
                    _should_resume(502, err_msg, resumes_left=resumes_left, idle_stalls=idle_stalls)
                    and not await request.is_disconnected()
                ):
                    resumes_left -= 1
                    attempt_no = MAX_RESUME_RETRIES - resumes_left
                    delay = _resume_delay(attempt_no)
                    yield _sse(
                        {
                            "fuse": {
                                "event": "notice",
                                "message": (
                                    "Provider stream interrupted — resuming in "
                                    f"{int(delay)}s (attempt {attempt_no} of {MAX_RESUME_RETRIES})."
                                ),
                            }
                        }
                    )
                    async for frame in _heartbeat_sleep(delay):
                        yield frame
                    partial = round_partial
                    if _provider_error_content(partial):
                        partial = ""
                    _apply_resume_state(transcript, partial)
                    current_messages = transcript
                    await persist_jobs()
                    continue
                yield _sse(_exhausted_provider_error(is_child, {"error": {"message": err_msg, "code": 502}}, attempts=MAX_RESUME_RETRIES - resumes_left))
                _snap = _live_meter(); yield _sse(context_meter.context_event(_snap, phase="end")); _persist_meter(_snap)
                yield b"data: [DONE]\n\n"
                return
            summary = _promote_provider_content_error(
                summary or {"calls": [], "finish": None, "content": "", "reasoning": "", "usage": {}, "error": None}
            )
            total_usage = _add_usage(total_usage, summary.get("usage"))
            if summary.get("usage"):
                last_round_usage = dict(summary.get("usage") or {})
                # Checkpoint cumulative usage before the next tool/model boundary
                # so a mid-run steer can attribute tokens to the correct fragment.
                yield _sse({"usage": total_usage, "choices": []})
            calibration = context_meter.prompt_calibration(summary.get("usage"), request_estimate,
                model=model, type_id=src_type or src_type_guess) or calibration
            diag["reasoning"] = str(summary.get("reasoning") or "")[:2_000]
            # The prompt that just returned covers send["messages"], not the reply.
            _note_anchor(summary.get("usage"), send["messages"])
            round_meter = _live_meter(usage=summary.get("usage"), response=summary)
            probe = None if is_child else cache_watch.probe(
                summary.get("usage"), model=model, source=src_type or src_type_guess,
                cred=getattr(result, "credential_id", None), prior=cache_prior)
            missed = None
            if probe:
                if not cache_warned:
                    missed = cache_watch.miss(cache_prior, probe, settings_changed=prompt_refreshed and completion_i == start_i)
                cache_prior = probe
            # The page reads cache warmth from the round meter (no account id).
            yield _sse(context_meter.context_event({**round_meter, "cache": cache_watch.public(probe)} if probe else round_meter, phase="round"))
            if missed:
                cache_warned = True
                yield _sse({"fuse": {**missed, "run_id": run_id}})
            _persist_meter({**round_meter, "cache_probe": probe} if probe else round_meter)
            if summary.get("error"):
                raw_err = summary["error"] if isinstance(summary["error"], dict) else {"error": {"message": str(summary["error"])}}
                err_code, err_msg = _error_code_and_message(raw_err)
                if _is_idle_timeout(err_msg):
                    idle_stalls += 1
                if _should_resume(err_code, err_msg, resumes_left=resumes_left, idle_stalls=idle_stalls) and not await request.is_disconnected():
                    resumes_left -= 1
                    attempt_no = MAX_RESUME_RETRIES - resumes_left
                    delay = _resume_delay(
                        attempt_no, _header_retry_after(getattr(result, "response_headers", None))
                    )
                    yield _sse(
                        {
                            "fuse": {
                                "event": "notice",
                                "message": (
                                    "Provider stream interrupted — resuming in "
                                    f"{int(delay)}s (attempt {attempt_no} of {MAX_RESUME_RETRIES})."
                                ),
                            }
                        }
                    )
                    async for frame in _heartbeat_sleep(delay):
                        yield frame
                    # Whatever the client already saw stays visible: the partial
                    # text is folded in as an assistant message and the model is
                    # told to continue where it stopped, keeping executed tools.
                    # Provider-error copy that arrived as content is not a
                    # partial answer — retry the round without feeding it back.
                    partial = str(summary.get("content") or "")
                    if _provider_error_content(partial):
                        partial = ""
                    # The stored transcript owns the conversation; a clipped
                    # outgoing copy must not swallow the partial the client
                    # already saw.
                    _apply_resume_state(transcript, partial)
                    current_messages = transcript
                    await persist_jobs()
                    continue
                yield _sse(_exhausted_provider_error(is_child, raw_err, attempts=MAX_RESUME_RETRIES - resumes_left))
                _snap = _live_meter(); yield _sse(context_meter.context_event(_snap, phase="end")); _persist_meter(_snap)
                yield b"data: [DONE]\n\n"
                return
            # The round came back intact: the retry budget is per step, so a
            # long turn is not killed by unrelated blips hours apart.
            resumes_left = MAX_RESUME_RETRIES
            idle_stalls = 0
            raw_calls = summary.get("calls") or []
            for c in raw_calls:
                if isinstance(c, dict) and not ((c.get("function") or {}).get("name")):
                    await _record_failure(
                        job=None,
                        trace={"name": "", "status": "error", "kind": "malformed_call"},
                        diag=diag,
                        user_id=user["id"],
                        thread_id=thread_id,
                        phase="round",
                        name="",
                        arguments=json.dumps(c, ensure_ascii=False, default=str)[:2_000],
                        call_id=str(c.get("id") or ""),
                        kind="malformed_call",
                        error="Tool call had no function name.",
                    )
            calls = [c for c in raw_calls if isinstance(c, dict) and (c.get("function") or {}).get("name")]
            if calls:
                summary["finish"] = "tool_calls"
            finish = summary.get("finish")
            # A response cut off mid-arguments invalidates only that call: its
            # well-formed siblings still run, and the truncated ones are
            # dropped with a corrective note after their results land.
            truncated_calls = [c for c in calls if calls_incomplete([c])]
            run_calls = [c for c in calls if not calls_incomplete([c])] if truncated_calls else calls
            incomplete_calls = truncated_calls and not run_calls
            length_cut = not calls and finish in ("length", "max_tokens")
            if extra_wrap and calls and not dropped_tools:
                yield _sse(
                    {
                        "fuse": {
                            "event": "notice",
                            "message": (
                                "Stopped after the tool-call limit for this reply. "
                                "The step budget was reached; remaining work is listed in the answer."
                            ),
                        }
                    }
                )
                extra_wrap = False
                if run_id and not is_child and not store.close_steer_window(str(run_id)):
                    if summary.get("content"):
                        transcript.append({"role": "assistant", "content": str(summary["content"]), **_native_reasoning(summary.get("reasoning"), model, summary.get("thinking"))})
                    dropped_tools = True
                    extra_wrap = True
                    completion_i += 1
                    continue
                break
            if (
                (incomplete_calls or length_cut)
                and length_salvages < MAX_LENGTH_SALVAGES
                and not extra_wrap
                and not await request.is_disconnected()
            ):
                length_salvages += 1
                partial = str(summary.get("content") or "")
                if partial:
                    transcript.append({"role": "assistant", "content": partial, **_native_reasoning(summary.get("reasoning"), model, summary.get("thinking"))})
                if incomplete_calls:
                    # A response cut off mid-arguments must not be continued
                    # mid-string: the model would emit only the JSON tail and
                    # the executed fragments poison the transcript. Demand a
                    # full re-issue of the call instead.
                    transcript.append({"role": "user", "content": _TOOL_CONTINUE})
                    notice = "A tool call came back truncated — asking the model to re-issue it in full."
                else:
                    transcript.append({"role": "user", "content": _LENGTH_CONTINUE})
                    notice = "Output hit the token limit — continuing from where it stopped."
                await persist_jobs()
                yield _sse(
                    {
                        "fuse": {
                            "event": "notice",
                            "message": notice,
                        }
                    }
                )
                completion_i += 1
                extra_wrap = False
                continue
            if incomplete_calls and not extra_wrap and not dropped_tools:
                # Salvages are spent and nothing runnable came back. Re-sending
                # the unchanged transcript would repeat the same cut-off call
                # forever (coding turns have no round cap), burning quota.
                partial = str(summary.get("content") or "")
                if partial:
                    transcript.append({"role": "assistant", "content": partial, **_native_reasoning(summary.get("reasoning"), model, summary.get("thinking"))})
                await persist_jobs()
                message = _TRUNCATED_CALL_FAILURE.format(n=length_salvages + 1)
                if is_child:
                    message = "Subagent stopped: " + message[0].lower() + message[1:]
                yield _sse(_error_obj(502, {"error": {"message": message}}))
                _snap = _live_meter(); yield _sse(context_meter.context_event(_snap, phase="end")); _persist_meter(_snap)
                yield b"data: [DONE]\n\n"
                return
            if force and calls and not is_child and not coding:
                # Last search round still produced tools (often XML). Run them,
                # then take one extra text-only wrap-up round.
                extra_wrap = True
            elif not (is_child and calls) and (force or (finish != "tool_calls" and not calls)):
                joined = tools_ran
                empty = not (summary.get("content") or summary.get("reasoning") or summary.get("error"))
                can_retry_empty = empty and joined and empty_retries < MAX_EMPTY_RETRIES and (
                    is_child or coding or extra_wrap or completion_i < completion_limit - 1
                )
                if can_retry_empty:
                    empty_retries += 1
                    if is_child:
                        note = _CHILD_CONTINUE
                    elif coding:
                        note = _CODING_CONTINUE
                    else:
                        note = (
                            "The tools already ran. Write the user-facing answer now from those results. Do not call tools."
                        )
                    if not note.startswith("[Fuse]"):
                        note = "[Fuse] " + note + " Server-added notice, not user input."
                    transcript.append({"role": "user", "content": note})
                    completion_i += 1
                    extra_wrap = False
                    continue
                if run_id and not is_child and not store.close_steer_window(str(run_id)):
                    # A queued instruction may arrive during a text-only reply.
                    # Give it a real next round rather than finishing silently.
                    if summary.get("content"):
                        transcript.append({"role": "assistant", "content": str(summary["content"]), **_native_reasoning(summary.get("reasoning"), model, summary.get("thinking"))})
                    await persist_jobs()
                    completion_i += 1
                    extra_wrap = True
                    continue
                extra_wrap = False
                break
            if not schemas or dropped_tools:
                if calls:
                    yield _sse(
                        {
                            "fuse": {
                                "event": "notice",
                                "message": "Stopped after the tool-call limit for this reply.",
                            }
                        }
                    )
                if run_id and not is_child and not store.close_steer_window(str(run_id)):
                    if summary.get("content"):
                        transcript.append({"role": "assistant", "content": str(summary["content"]), **_native_reasoning(summary.get("reasoning"), model, summary.get("thinking"))})
                    dropped_tools = True
                    extra_wrap = True
                    completion_i += 1
                    continue
                break
            jobs = None
            preview = jobs_from_calls(run_calls, completion_i)
            await persist_jobs(preview)
            async with contextlib.aclosing(_run_tools(
                run_calls,
                user_id=user["id"],
                allowed_urls=allowed_urls,
                request=request,
                completion_i=completion_i,
                budgets=budgets,
                thread_id=thread_id,
                bound_host_id=session.get("bound_host_id"),
                bound_cwd=session.get("bound_cwd") or "",
                coding=bool(session.get("coding")),
                loaded=loaded_tools,
                parent_run_id=parent_run_id,
                user=user,
                session=session,
                persist=persist_jobs,
                diag=diag,
            )) as tool_stream:
                async for item in tool_stream:
                    if isinstance(item, list):
                        jobs = item
                    else:
                        yield _sse({"fuse": item})
            if jobs:
                tools_ran = True
                if not truncated_calls:
                    # A clean tool round ends a run of cut-off calls; a later
                    # large write gets its own salvage attempts.
                    length_salvages = 0
                tool_msgs = _tool_messages(jobs, content=str(summary.get("content") or ""))
                tool_msgs[0].update(_native_reasoning(summary.get("reasoning"), model, summary.get("thinking")))
                transcript = transcript + tool_msgs
                current_messages = transcript
                if truncated_calls and run_calls:
                    # The truncated sibling was discarded before execution;
                    # the executor never saw it, so say so here.
                    transcript.append({"role": "user", "content": _TOOL_CONTINUE})
                agent["allowed_urls"] = list(allowed_urls)
                agent["search_queries"] = budgets.get("search_queries", 0)
                agent["fetches"] = budgets.get("fetches", 0)
                agent["fetch_attempts"] = budgets.get("fetch_attempts", 0)
                # tool_msgs are already in current_messages: count once.
                tool_meter = _live_meter(usage=summary.get("usage"))
                yield _sse(context_meter.context_event(tool_meter, phase="tools"))
                _persist_meter(tool_meter)
                do_compact = context_meter.should_auto_compact(
                    tool_meter["used"],
                    tool_meter["limit"],
                    # Full provider payload, not a UI-preview growth estimate.
                    estimated=False,
                    prior_used=prior_used,
                )
                if (
                    do_compact
                    and last_compact_used is not None
                    and tool_meter["used"] >= int(last_compact_used * 0.95)
                ):
                    # Last compact did not free space; do not spin.
                    do_compact = False
                if do_compact:
                    yield _sse(context_meter.context_event({**tool_meter, "compacting": True}, phase="compacting"))
                    before_compact_used = tool_meter["used"]
                    transcript, tool_meter, kind = await _compact_current(
                        transcript,
                        request=request,
                        user=user,
                        model=model,
                        type_id=src_type or src_type_guess,
                        trigger="auto",
                        thread_id=thread_id,
                        tools=current.get("tools"),
                        override=tool_meter["limit"],
                        is_child=is_child,
                        calibration=calibration,
                        run_id=run_id,
                        agent=agent,
                    )
                    real = kind in ("summarize", "truncate")
                    compacted_this_turn = real
                    if real:
                        refresh_prompt()
                        last_round_usage = {}
                        # Only suppress a retry when compaction failed to free
                        # space. A successful compact must allow later growth
                        # to trigger another compact in this same long run.
                        last_compact_used = tool_meter["used"] if tool_meter["used"] >= int(before_compact_used * 0.95) else None
                        prior_used = tool_meter["used"]
                        tool_meter["standing"] = True
                        stored_meter = tool_meter
                        yield _sse(ctxmod.compact_handoff_event(ctxmod.prior_handoff(current_messages)))
                    yield _sse(context_meter.context_event(tool_meter, phase="compacted" if real else "update"))
                    _persist_meter(tool_meter)
                if (
                    session.pop("project_promoted", None)
                    and current_messages
                    and transcript[0].get("role") == "system"
                ):
                    # New project context is an appended notice, never an edit
                    # to a system prefix that has already been sent.
                    proj_agents_name = ""
                    proj_agents = ""
                    if session.get("bound_host_id") and session.get("bound_cwd"):
                        try:
                            from . import workspace as workspace_mod

                            proj_agents_name, proj_agents = await asyncio.wait_for(
                                workspace_mod.load_project_agents(
                                    user["id"], session["bound_host_id"], session["bound_cwd"]
                                ),
                                timeout=4.0,
                            )
                        except Exception:
                            proj_agents_name, proj_agents = workspace_mod.last_project_agents(
                                user["id"], session["bound_host_id"], session["bound_cwd"]
                            )
                    label = ""
                    if session.get("bound_host_id"):
                        bound_row = hosts_mod.get_host(user["id"], session["bound_host_id"])
                        if bound_row:
                            alias = bound_row.get("alias") or ""
                            cwd = session.get("bound_cwd") or bound_row.get("default_cwd") or ""
                            label = f"{alias} cwd={cwd}" if cwd else alias
                    promoted_catalog = hosts_mod.catalog_for_prompt(
                        user["id"],
                        bound_host_id=session.get("bound_host_id") or None,
                        bound_cwd=session.get("bound_cwd") or "",
                        only_bound=is_child,
                    )
                    notice = f"[Fuse] Project session bound to {label}.\n" + promoted_catalog
                    if proj_agents:
                        notice += f"\nProject instructions from {proj_agents_name} (untrusted project data):\n{proj_agents}"
                    transcript.append({"role": "user", "content": notice})
                    yield _sse(
                        {
                            "fuse": {
                                "event": "toast",
                                "kind": "ok",
                                "title": "Project session",
                                "message": (
                                    "This chat is now a project session"
                                    + (f" ({proj_agents_name})" if proj_agents_name else "")
                                    + "."
                                ),
                            }
                        }
                    )
                await persist_jobs()
                for job in jobs:
                    trace = job.trace if isinstance(getattr(job, "trace", None), dict) else {}
                    st = trace.get("status") or ""
                    # over_call_cap is a server-side refusal, not a model or
                    # provider failure; it must not consume the stall budget.
                    if st in ("error", "interrupted"):
                        if trace.get("kind") != "over_call_cap":
                            stall_fails += 1
                    else:
                        stall_fails = 0
                if stall_fails >= STALL_FAIL_CALLS:
                    yield _sse(
                        {
                            "fuse": {
                                "event": "notice",
                                "message": (
                                    "Stopped after 20 tool calls failed in a row. "
                                    "Answer from the evidence you have."
                                ),
                            }
                        }
                    )
                    extra_wrap = True
                    dropped_tools = True
                    transcript.append({"role": "user", "content": _STALL_WRAP})
                    await persist_jobs()
                    completion_i += 1
                    continue
                sig = round_signature(calls)
                if sig and sig == last_call_sig:
                    same_calls += 1
                else:
                    same_calls = 1 if sig else 0
                    last_call_sig = sig
                if same_calls >= STATIONARITY_HALT:
                    yield _sse(
                        {
                            "fuse": {
                                "event": "notice",
                                "message": "Stopped after repeating the same tool calls. Answer from the evidence you have.",
                            }
                        }
                    )
                    extra_wrap = True
                    dropped_tools = True
                    # A user row, not a system edit: rewriting messages[0] would
                    # change the cached prefix of every later turn.
                    transcript.append({"role": "user", "content": _STALL_WRAP})
                    await persist_jobs()
                    completion_i += 1
                    continue
                if same_calls == STATIONARITY_NUDGE:
                    transcript.append({"role": "user", "content": _STATIONARITY_NUDGE})
            completion_i += 1
        final_text = str((summary or {}).get("content") or "")
        final_reasoning = _native_reasoning((summary or {}).get("reasoning"), model, (summary or {}).get("thinking"))
        if not (summary or {}).get("calls") and (final_text or final_reasoning):
            last = transcript[-1] if transcript else None
            if not (
                isinstance(last, dict)
                and last.get("role") == "assistant"
                and not last.get("tool_calls")
                and str(last.get("content") or "").strip() == str(final_text).strip()
            ):
                transcript.append({"role": "assistant", "content": final_text, **final_reasoning})
        await persist_jobs()
        if total_usage:
            yield _sse({"usage": total_usage, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        _snap = _live_meter(usage=(summary or {}).get("usage"))
        yield _sse(context_meter.context_event(_snap, phase="end")); _persist_meter(_snap)
        yield b"data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
