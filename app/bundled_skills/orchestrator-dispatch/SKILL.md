---
name: orchestrator-dispatch
description: Dispatch clones or cheaper subagents. Use only when the user asks to spawn subagents, delegate, or orchestrate. Confirm once; keep parent thin.
argument-hint: job or slice
license: MIT
---

# Orchestrator Dispatch

The lead is a dispatcher. Workers are the arms and legs.

Do not apply this skill unless the user asked to use subagents or delegate work. A large job alone is not enough.

Goal: keep the expensive parent thin. Do not spend frontier tokens walking the repo, grepping, tracing, or implementing the slice. Plan, dispatch, glance, move on.

Tool-agnostic. Use the host spawn. If none exists, run separate sessions and paste only the schema block back.

## When to spawn

**Do it yourself:** Tiny work (about 1–2 lines or a short lookup) and **fragile or high-stakes work** (live DB, prod, irreversible, security/authz).

**Spawn:** likely ~10+ tool calls — definitely if ~20+. Any kind of job. Size, not type.

Workers (peer/clone **and** cheap models) may do **any** of those jobs. Do not reserve implementation for the lead.

## Orient, then confirm once

Do not spawn until the user says yes (or picks A/B/C). Confirm **once**. After that, dispatch freely — do not re-ask per slice.

Before offering the plan (yes/no or A/B/C), the lead must already have a **high-level** picture (layout, relevant dirs/modules, how the slice fits). A short map, not a full read. Do not orchestrate blindly.

1. Orient. Name the slices. Say read vs write, sequential vs parallel, peer vs cheap.
2. Show the plan in the parent chat. Short. Then stop.
3. One clearly right way → that plan, **yes or no**.
4. Two or three real forks → **A / B / C**. Never more than three. Default first. Ask them to pick ("go ahead" / "use B" / "use C").
5. Wait. No workers until they confirm.
6. After yes, run that plan. Do not silently switch mode, model, or fan-out. Do not confirm again.

Offer a fork only if it would change the result:

- **Worker model** — same-model clones (peer) vs cheap workers
- **Shape** — parallel read-only vs sequential writes vs parallel writes on disjoint files

If the user already named a fork, do not re-ask it. Still show the slice plan and get a yes. Ask in the parent chat, not inside a worker.

## Roles

**Lead** — orient, slice, dispatch, record. Keep the parent thin: plan, DONE, TESTS, next slice. Do not re-walk the tree. Do not re-do a signed-off slice.

**Worker** — own the slice. Do the assigned job (any type). Return what changed and where. Run tests when the slice has them. Never spawn.

First line of every worker brief:

```
YOU ARE THE WORKER. DO NOT SPAWN. Own this slice. Do the work. Sign it off when done. Return what changed, which files, leftovers, and blockers. Do not ask the lead to finish the slice.
```

## Mode

Default one mode per run. Mix only when a slice clearly needs a peer. Both modes do the full job.

| Mode | When | Worker does |
| --- | --- | --- |
| cheap | worker weaker than lead | any task; return what/where; lead thin-reviews |
| peer | same model as lead, or about as capable (clone) | any task; own the slice; sign off with DONE; lead trusts the citation |

Same-model worker → peer. Weaker → cheap. Unsure → cheap.

Same-model clone: parent context, not a cheaper brain. Parent plans. Clones work and sign off. Parent records DONE and dispatches the next.

## Sequential vs parallel

**Read** (audit, review, debug, search): prefer parallel. **Write** (implement): sequential unless slices do not share files.

Parallel writes only if slices are disjoint — name the files; if they might race, sequence them. Same-file parallel writes clobber.

Implementation: parent writes the plan once; workers execute; parent keeps plan + DONE + next slice.

## Dispatch

One job per spawn. Named paths or symbols. No history dump. Cap 8 live workers; more waves beat unbounded fan-out. Named files, exact task. Do not show workers other slices’ results.

Stop a slice on: complete schema (including DONE); ~8k–12k worker tokens; two follow-ups with no progress.

Failure (timeout, crash, empty, malformed, COVERED << SLICE): one tighter resend ("schema only"), then the lead does that slice, or mark it uncovered. Partial cover is not success.

Workers escalate only for blockers: missing access, security/authz/data-loss, or a job that does not fit the assigned files. Do not escalate because the approach is unclear — do the work, then leftover. Do not dump the slice back to the lead.

## Worker output

```
SLICE: paths or symbols
COVERED: what was actually read or edited
DONE: what changed | files | leftover
TESTS: what ran | result (or none)
BLOCKERS: none, or what the lead must handle
```

Read-only DONE is `none | — | leftover questions`.

Findings need `path:lines` (or off-code `source | excerpt`). Implementation DONE needs files, not a quote of every edit. Do not accept "I fixed it" with no files.

## Lead review

Default pass: **glance at the summary + tests**. Do not re-read the slice. Do not re-implement it.

**Cheap workers — trust, then thin-check:**

1. First couple of slices: actually open the cited files and skim. Confirm quality is acceptable.
2. If those pass, later slices: summary + tests only. Still read DONE and TESTS every time.
3. Never tell the worker you stopped spot-checking.

**Peer/clone:** trust well-cited DONE. Open files only for BLOCKERS, collisions, or security/data-loss.

Always: missing files/quote → drop or one tighter resend.

## Optional audit wave

Independent slices → parallel workers → merge → re-dispatch only blockers, failures, or uncovered dirs. Lead writes the merged audit. Workers may ship slice findings; lead owns the merge.

```
FINDINGS:
- id | path:lines | failure mode | why (lead's words)
GAPS:
- what workers did not cover
DONE:
- slice | summary | next
```

## Quality floor

No files on DONE, or no quote on a finding → drop. Conflict → change scope; do not resend the same brief. Child that plans the whole repo → kill, tighter slice. Failure → one tighter resend, then the lead does that slice, or mark it uncovered. Security/conflict → open those files (or a tighter spawn), not the whole slice.

## Anti-patterns

Spawning before the one confirm. Orchestrating before a high-level map. Re-confirming every slice. Re-reading or re-doing a signed-off slice. Delegating a 1–2 line edit. Delegating live-DB / prod / irreversible / security work. Lead doing a ~10+ tool-call job itself. Parallel writes on the same file. Nested orchestrators. Telling a worker you are not reviewing. Blind retry of a timed-out or malformed worker. Pulling the whole diff into the parent. Loading this skill when the user did not ask to spawn or delegate.
