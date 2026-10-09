"""One drain for a live run's inbox, at each tool boundary.

A run's inbox is what arrives while it generates: user steers (root runs),
parent follow-ups (subagents), and background results — subagent reports,
finished background commands, monitor events — plus, on a root run's user
turn, reports and command results that finished while nothing was running.
Every kind is queued durably on the run row, peeked here, and acknowledged
by save_turn_progress in the transaction that persists the transcript
containing it. Arrival also signals run_inbox so a blocked wait returns.

Background results reach the model as one framed user message and the user
as one `delivery` segment at the same point of the reply; wake turns that
carry results while idle render the same rows from their own message.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

RESULTS_HEAD = "[Fuse] AUTOMATED EVENT — not written by the user. Background results:"
RESULTS_TAIL = (
    "These are for you, not the user, and not independent verification. "
    "A <monitor_event> means the watched condition matched: act on it, and do not start another watch for it. "
    "A <monitor_result> means that watch ended: state=\"completed\" is the normal end; "
    "\"timed_out\" means the condition never held, so find out why and re-arm only if you still need it. "
    "Do not re-arm a firehose auto-stop; narrow the pattern instead."
)
FOLLOWUP_HEAD = (
    "[Fuse] Follow-up from the parent — not written by the end user. "
    "Stay in the assigned task and host/cwd unless this follow-up changes them:"
)

# The display copy of one delivery is clipped per item: the model got the
# whole envelope; the row shows a preview and the agent pane has the rest.
DISPLAY_ITEM_MAX = 6_000
DISPLAY_TAIL = 400
DISPLAY_MAX = 48_000
_START = re.compile(r'<(task|command_result|monitor_event|monitor_result) id="([^"]*)"[^>]*>')


@dataclass
class Drain:
    results: list[str] = field(default_factory=list)
    command_ids: list[str] = field(default_factory=list)
    steers: list[dict] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)


def _command_result_ids(envelopes: list[str]) -> list[str]:
    from .tool_jobs import _command_result_ids as ids

    return ids(envelopes)


def drain(store, *, run_id: str, is_child: bool, user_id: int, thread_id, transcript, delivered_local: set[str]) -> Drain:
    """Peek everything waiting for this run. Non-destructive: delivered_local
    dedupes within the turn until save_turn_progress acknowledges."""
    out = Drain()
    # Children drain results too: a nested parent must see its own
    # grandchildren's results, not just the root.
    results = [t for t in store.take_child_results(run_id) if t not in delivered_local]
    delivered_local.update(results)
    # Command/monitor envelopes carry <command_result> / <monitor_event> /
    # <monitor_result> markers; their receipt commits with the transcript.
    out.command_ids = _command_result_ids(results)
    if not results and not is_child and thread_id:
        # Results that finished while no run was generating (owner idle or
        # service restarted), plus subagent reports stranded by a hard user
        # stop or exhausted wake retries: they ride with the user's next turn
        # instead of starting an automated one. queue_stranded_reports stashes
        # them via queue_child_result so the acknowledgment (and the agent
        # panel's delivered state) stays on the live-injection path.
        try:
            from .. import command_jobs

            pending = command_jobs.pending_envelopes(user_id, int(thread_id))
            out.command_ids = [item["id"] for item in pending]
            results = [item["envelope"] for item in pending]
        except Exception:
            logger.debug("thread command result inject skipped", exc_info=True)
        try:
            # An automated wake turn already carries its reports in its seed;
            # they stay candidates until that turn completes, so they must not
            # be injected again.
            results.extend(store.queue_stranded_reports(user_id, int(thread_id), run_id, transcript))
        except Exception:
            logger.debug("thread subagent result inject skipped", exc_info=True)
    out.results = results
    if not is_child:
        steers = store.take_steers(run_id)
        out.steers = [item for item in steers if f"(steer:{item.get('id')})" not in delivered_local]
        delivered_local.update(f"(steer:{item.get('id')})" for item in out.steers)
    else:
        followups = [t for t in store.take_followups(run_id) if t not in delivered_local]
        delivered_local.update(followups)
        out.followups = followups
    return out


def results_message(envelopes: list[str]) -> dict:
    """The provider-side message carrying background results."""
    return {"role": "user", "content": RESULTS_HEAD + "\n\n" + "\n\n".join(envelopes) + "\n\n" + RESULTS_TAIL}


def followup_message(followups: list[str]) -> dict:
    return {"role": "user", "content": FOLLOWUP_HEAD + "\n\n" + "\n\n".join(followups)}


def _clip(envelope: str) -> str:
    if len(envelope) <= DISPLAY_ITEM_MAX:
        return envelope
    head = envelope[: DISPLAY_ITEM_MAX - DISPLAY_TAIL]
    # The tail keeps the closing tags, so the client parses a clipped item.
    return head + "\n… [clipped for display]\n" + envelope[-DISPLAY_TAIL:]


def envelope_items(text: str) -> list[str]:
    """Each result element in text, in order. A report may quote `</task>`
    itself, so a task ends at its own `</task>` + `task_id: <id>` line when
    present (static/chat/traces/semantics.js parses the same way)."""
    out, pos = [], 0
    while True:
        match = _START.search(text, pos)
        if not match:
            return out
        tag, ident = match.group(1), match.group(2)
        close = f"</{tag}>"
        end = -1
        if tag == "task":
            anchor = text.find(f"{close}\ntask_id: {ident}", match.end())
            if anchor >= 0:
                line_end = text.find("\n", anchor + len(close) + 1)
                end = len(text) if line_end < 0 else line_end
        if end < 0:
            found = text.find(close, match.end())
            if found < 0:
                return out
            end = found + len(close)
        out.append(text[match.start():end])
        pos = end


def display_text(envelopes: list[str]) -> str:
    """The envelopes as the `delivery` segment stores them: tags only (no
    model-facing preamble), each clipped, the whole bounded."""
    items = [_clip(item) for env in envelopes for item in envelope_items(env)]
    out, used = [], 0
    for item in items:
        if used + len(item) > DISPLAY_MAX:
            break
        out.append(item)
        used += len(item) + 1
    return "\n".join(out)
