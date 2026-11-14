// Prompt library catalog (static/chat/composer/library.js). Bodies use
// {{Label}} for a required blank and {{Label|default}} for a prefilled field;
// footers are the ground rules per mode. tests/test_prompt_catalog.py checks it.
export const CATALOG = {
 "version": 1,
 "footers": {
  "audit": {
   "label": "Report only",
   "short": "Report",
   "text": "Ground rules:\n- Read-only: do not edit files, run state-changing commands (installs, migrations, git writes, restarts), or commit.\n- For each finding give: severity · `path:line` · what is wrong · the input or condition that triggers it · the fix · the impact if left. Tag it [confirmed] if you traced or reproduced it, otherwise [suspected].\n- Use the severity scale named above if there is one, otherwise Critical (exploitable, data loss, silent wrong results) / High (likely bug or serious risk) / Medium (edge case or fragile pattern) / Low (minor).\n- Real findings only, worst first. An area with nothing to report gets one line, not filler.\n- Finish with coverage (what you read, what you skipped and why) and a fix plan split into independent batches, then stop and ask which batches to apply."
  },
  "audit_fix": {
   "label": "Report, then fix",
   "short": "Report + fix",
   "text": "Ground rules:\n- Report first, before changing anything.\n- For each finding give: severity · `path:line` · what is wrong · the input or condition that triggers it · the fix · the impact if left. Tag it [confirmed] if you traced or reproduced it, otherwise [suspected].\n- Use the severity scale named above if there is one, otherwise Critical (exploitable, data loss, silent wrong results) / High (likely bug or serious risk) / Medium (edge case or fragile pattern) / Low (minor).\n- Real findings only, worst first. An area with nothing to report gets one line, not filler.\n- Then fix every confirmed Critical and High finding with the smallest correct change, verify each with the narrowest relevant test or check, and list the rest as follow-ups. Confirm a [suspected] finding before fixing it. Follow the project's instructions on committing."
  },
  "read": {
   "label": "Read-only",
   "short": "Read-only",
   "text": "Ground rules: read-only. Do not edit files or run state-changing commands. Cite code as `path:line`, and say \"unclear\" instead of guessing at intent."
  },
  "plan": {
   "label": "Plan first",
   "short": "Plan",
   "text": "Ground rules: plan only. Read and run whatever read-only checks you need, but do not edit files or run state-changing commands. End with the decisions I need to make, then wait for my go-ahead."
  },
  "act": {
   "label": "Make the change",
   "short": "Changes code",
   "text": "Ground rules:\n- Read the code you will touch, its callers, and its tests before changing anything.\n- Make the smallest change that fully solves it: no unrelated refactors, renames, or new dependencies.\n- Verify with the project's own test, lint, and build commands, narrowest first. Never weaken, skip, or delete a test to get green.\n- Report what changed and why, what you verified and how, and what is left open. Follow the project's instructions on committing."
  },
  "talk": {
   "label": "Conversation",
   "short": "Conversation",
   "text": "Ground rules: this is a conversation. Do not edit files. Ask before assuming, and keep each reply short enough to answer."
  }
 },
 "modifiers": [
  {
   "id": "subagents",
   "label": "Parallel subagents",
   "modes": [
    "audit",
    "audit_fix",
    "read"
   ],
   "text": "Scale: split the work into independent areas (by module or concern) and run read-only explore subagents in parallel, one per area, each with these same instructions and output format. Merge and dedupe their reports, and re-verify every Critical and High finding yourself before including it."
  },
  {
   "id": "concise",
   "label": "Terse",
   "modes": [
    "audit",
    "audit_fix",
    "read",
    "plan",
    "act",
    "talk"
   ],
   "text": "Keep it terse: one or two lines per item, no prose sections, no restating the code."
  },
  {
   "id": "artifact",
   "label": "Report as artifact",
   "modes": [
    "audit",
    "audit_fix",
    "read",
    "plan"
   ],
   "text": "Also publish the final report as an artifact page, grouped by severity or section; keep the chat reply to a short summary."
  }
 ],
 "categories": [
  {
   "id": "build",
   "name": "Fix & build",
   "icon": "wrench",
   "blurb": "Fix a bug, build a feature, get the tests green, upgrade safely.",
   "prompts": [
    {
     "id": "fix-bug",
     "title": "Find and fix a bug",
     "blurb": "Reproduce it, find the root cause, fix it, and prove it stays fixed.",
     "modes": [
      "act",
      "plan"
     ],
     "tags": [
      "debugging"
     ],
     "needs": "project",
     "body": "Bug: {{What happens, and what should happen instead}}\nHow to trigger it: {{Steps, input, or error message|unknown, find a reproduction}}\n\nFix this bug at its root cause.\n1. Reproduce it first with a failing test, script, or command. If you cannot, say what you tried and what you need from me before changing code.\n2. Isolate the cause: trace from the symptom to the line where behavior first goes wrong. State the root cause in one sentence and why it produces exactly this symptom.\n3. Check the blast radius: other callers or code paths with the same flaw.\n4. Fix the cause, not the symptom. No catch-all handlers, retries, or special cases that hide it.\n5. Turn the reproduction into a regression test that fails before the fix and passes after, then run the related tests.",
     "needs_input": true
    },
    {
     "id": "build-feature",
     "title": "Build a feature end to end",
     "blurb": "Implement it the way the codebase already works, with tests.",
     "modes": [
      "act",
      "plan"
     ],
     "tags": [
      "feature"
     ],
     "needs": "project",
     "body": "Feature: {{What it should do, and for whom}}\nDone means: {{Acceptance criteria|works end to end, is tested, and matches the existing code}}\n\nBuild this feature.\n1. Read the code it touches and find the patterns it should follow (structure, naming, error handling, tests). Reuse what exists before adding anything.\n2. If a decision would materially change the design (data model, public API, UX) and the request does not settle it, ask before building. Otherwise pick the simplest option and say so.\n3. Put the steps in a todo list, then implement in small increments that each leave the code working.\n4. Add tests at the level the project already uses: the main path, the edge most likely to break, and one failure path.\n5. Run the relevant tests, lint, and build, then exercise the feature once the way a user would.",
     "needs_input": true
    },
    {
     "id": "plan-change",
     "title": "Plan a change before coding",
     "blurb": "Files, steps, tests, and risks, before a line is written.",
     "modes": [
      "plan"
     ],
     "tags": [
      "planning"
     ],
     "needs": "project",
     "body": "Change: {{The change you want}}\n\nPlan how to implement this change.\n1. Read the code involved and summarize the current behavior in a few lines, with the files that own it.\n2. The approach: each file to change and what changes in it, any new pieces and where they live, and how data flows through the result.\n3. Order the work into steps that each leave the code working and testable.\n4. The tests to add or update, and how to verify the change end to end.\n5. Risks: what could break elsewhere, compatibility or migration concerns, and the riskiest step.\n6. If a meaningfully simpler alternative exists, give it in two lines and say which you recommend.",
     "needs_input": true
    },
    {
     "id": "fix-tests",
     "title": "Get failing tests green, honestly",
     "blurb": "Fix what is actually broken; never weaken a test.",
     "modes": [
      "act",
      "plan"
     ],
     "tags": [
      "testing",
      "debugging"
     ],
     "needs": "project",
     "body": "Failing: {{Test, CI job, or error|run the suite to find out}}\n\nGet these tests passing by fixing what is actually wrong.\n1. Run the failing tests (or the CI job's command) and collect every failure with its error.\n2. Group failures by root cause; one bug often fails many tests.\n3. For each group, decide with evidence whether the code or the test is wrong: read the test's intent and check history (`git log -S`, `git log -p` on the file) for a deliberate behavior change the test was not updated for.\n4. If it's a bug, fix the code. Update the test only if the behavior change was intentional, and keep what the test was guarding.\n5. Never skip, delete, loosen, or special-case a test to get green. Rerun until clean and report what each group was.",
     "needs_input": false
    },
    {
     "id": "upgrade-dep",
     "title": "Upgrade a dependency safely",
     "blurb": "Read the changelog, find affected call sites, upgrade, verify.",
     "modes": [
      "plan",
      "act"
     ],
     "tags": [
      "dependencies"
     ],
     "needs": "project",
     "body": "Package: {{Package name}}\nTarget version: {{Version|the latest stable}}\n\nUpgrade this dependency.\n1. Find the current version, where it is pinned, and every place the code uses it.\n2. Read the changelog and migration notes between the two versions (search the web for them). List each breaking or behavior change that touches our usage, with the affected call sites.\n3. Check knock-on effects: peer dependency bumps, lockfile changes, minimum runtime versions.\n4. Update each call site, update the lockfile with the project's own tool, and run the tests and build.\n5. Report behavior changes users could notice and how to roll back.",
     "needs_input": true
    },
    {
     "id": "regressions",
     "title": "Audit recent commits for regressions",
     "blurb": "Bugs the last few commits introduced, before users find them.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "audit",
      "review"
     ],
     "needs": "project",
     "body": "Range: {{Commits to audit|the last 20 commits on this branch}}\n\nAudit these already-committed changes for regressions. Read each diff (`git log -p`) together with the code around it, not just the lines that changed.\n1. For each commit, state what it meant to do and check that it does that, including on the paths it did not touch but changed the behavior of (callers, shared helpers, config, schema).\n2. Hunt what slips through review: changed defaults, renamed or removed fields still read elsewhere, reordered operations, error handling that got looser, conditions that got inverted, tests edited in the same commit to match new behavior.\n3. Check interactions between commits: a later change that silently undoes or conflicts with an earlier one.\n4. For each regression, name the commit, the input that now behaves differently, and the test that would have caught it.",
     "needs_input": false
    }
   ]
  },
  {
   "id": "review",
   "name": "Review & refactor",
   "icon": "review",
   "blurb": "Review the current change, or restructure code without changing behavior.",
   "prompts": [
    {
     "id": "review-diff",
     "title": "Review the current change",
     "blurb": "A demanding, fair senior review of your diff.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "review"
     ],
     "needs": "project",
     "body": "Change: {{Change|uncommitted work, or this branch against its base}}\n\nReview this change the way a demanding, fair senior engineer reviews a pull request. Get the diff and read the code around it. If there is no change to review, say so and stop.\n1. Intent: one sentence on what the change does (say if you inferred it). Flag unrelated edits and scope creep.\n2. Correctness: bugs it introduces or exposes (edge cases, error paths, concurrency, broken invariants), each tied to a concrete failing scenario.\n3. Design: is this the right place and shape? Duplication, leaky abstractions, or a clearly simpler structure.\n4. Blast radius: callers, compatibility, migrations, performance, security, observability.\n5. Tests: whether they cover the change, and the one missing test most likely to catch a regression.\nSeverity scale: Blocking / Should fix / Nit. Skip anything a linter would catch. End with a verdict: merge, merge after fixes, or rework.",
     "needs_input": false
    },
    {
     "id": "safe-refactor",
     "title": "Refactor without changing behavior",
     "blurb": "Small, revertible steps behind a pinned contract.",
     "modes": [
      "plan",
      "act"
     ],
     "tags": [
      "refactor"
     ],
     "needs": "project",
     "body": "Target: {{Target|the messiest high-traffic code you find (name the runners-up)}}\n\nRefactor this to be easier to read and change, without changing observable behavior.\n1. Pin the contract: inputs, outputs, side effects, errors, and the edge cases callers rely on. Keep behavior that looks accidental unless it is clearly a bug, and list it.\n2. Check the safety net: which tests cover this code. If coverage is thin, add characterization tests first.\n3. Choose the smells worth fixing (duplication, deep nesting, mixed responsibilities, unclear names) and say which you are leaving alone.\n4. Sequence it as small steps, each behavior-preserving and revertible on its own. Keep public signatures stable.\n5. Any step that would change behavior is a separate decision: flag it, do not fold it in.\n6. Run the covering tests after each step.",
     "needs_input": false
    },
    {
     "id": "hotspots",
     "title": "Audit complexity hotspots",
     "blurb": "The files that cost the most to change, ranked, with how to split them.",
     "modes": [
      "audit"
     ],
     "tags": [
      "audit",
      "refactor"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this repository}}\n\nFind the code that is most expensive to change and rank it as refactoring targets.\n1. Measure, don't guess: size (files over about 1,500 lines, functions over about 100), nesting depth and branch count, and churn from git (`git log --format= --name-only | sort | uniq -c | sort -rn`). Hotspots are where high complexity meets high churn.\n2. For each of the top hotspots: the responsibilities it has mixed together, the hidden couplings (shared mutable state, ordering, side effects) that make a split risky, and the natural seams to split along.\n3. Propose the target units for each, with one responsibility and a small interface each, and the first safe extraction.\n4. Say which large files are fine as they are (cohesive, rarely changed) so they are left alone.\nSeverity scale: rank by complexity × churn instead of severity.",
     "needs_input": false
    },
    {
     "id": "naming",
     "title": "Fix names and interfaces that mislead",
     "blurb": "Renames and signature fixes, ranked by confusion caused.",
     "modes": [
      "audit"
     ],
     "tags": [
      "review",
      "refactor"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|the public surfaces of this repository}}\n\nDo a naming and interface pass over this scope.\n1. Names that lie or under-describe: unclear booleans, vague nouns (data, info, manager, helper), hidden units (timeout in what?), costly abbreviations, names that no longer match behavior.\n2. Interface friction: parameters easy to swap, boolean flags that should be enums or separate functions, functions that do more than their name says, return values callers have to guess about.\n3. For each: the rename or signature change, a one-line reason, and how many call sites it touches.\n4. Match the codebase's existing conventions; don't import new ones.\nSeverity scale: Confusing (causes bugs or misreads) / Taste. Prefer a few high-impact changes over renaming everything.",
     "needs_input": false
    },
    {
     "id": "cleanup-plan",
     "title": "Plan a low-effort repo cleanup",
     "blurb": "Where things live, what is misplaced, and the cheapest moves to fix it.",
     "modes": [
      "audit"
     ],
     "tags": [
      "refactor",
      "documentation"
     ],
     "needs": "project",
     "body": "Audit how this repository is laid out and produce a cleanup plan a human can carry out in pieces. This is a tidy-up, not a rewrite.\n1. Map the layout: what lives where, what each top-level directory is for, and files that sit far from where they logically belong.\n2. Find the friction: the same kind of thing kept in several places (scripts, configs, fixtures, assets), tests that don't mirror the code they test, generated or local-only files that are tracked, and a root directory cluttered with one-offs.\n3. Cleanup items, biggest win for least effort first. For each: the move, the effort (S/M/L), the payoff, and every reference that needs updating (imports, scripts, CI, docs, ignore files). Someone who stops halfway should already have most of the value. No renames for taste, and no moves that force broad import churn.\nSeverity scale: effort and payoff instead of severity. List files you could not confidently categorize instead of guessing.",
     "needs_input": false
    },
    {
     "id": "dead-code",
     "title": "Find dead code and duplication",
     "blurb": "What can be deleted or merged, with evidence.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "refactor"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this repository}}\n\nFind code in this scope that can be deleted or merged.\n1. Dead code: functions, classes, routes, flags, config keys, CSS, and files with no live reference. Rule out dynamic use first (reflection, string dispatch, templates, entry points, scripts, tests, public API).\n2. Dependencies: declared packages nothing imports, and imports of packages that are not declared.\n3. Duplication: near-identical logic in several places that should share one implementation, and the reverse, forced sharing that couples unrelated code.\n4. Stale paths: feature flags that are always on or off, shims for versions no longer supported, TODOs about finished work.\nFor each: the evidence it is unused or duplicated, the safe removal or merge, and the risk if the evidence is wrong.",
     "needs_input": false
    }
   ]
  },
  {
   "id": "bugs",
   "name": "Bug hunting",
   "icon": "bug",
   "blurb": "Audit for the correctness, failure, data, and concurrency bugs the tests did not catch.",
   "prompts": [
    {
     "id": "full-audit",
     "title": "Audit the whole codebase, worst first",
     "blurb": "Correctness, security, data, and ops, ranked by severity.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "audit",
      "security"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|the whole repository}}\n\nAudit this end to end and report everything that is actually wrong.\n1. Understand it first: what the system is for, its main components, and how data and control flow through them. Write down what each part must guarantee before judging it.\n2. Plan coverage: list the areas you will review and track them, so nothing is silently sampled.\n3. Correctness: wrong logic, unhandled cases, broken invariants, error paths that swallow or mislabel failures, races, leaks, and places where a fundamentally better approach exists.\n4. Security: trust boundaries, input validation, authentication and authorization, data exposure, secrets, dependencies with known advisories.\n5. Data: writes that can corrupt, duplicate, or lose data; migrations and schema drift.\n6. Operations: config that is unsafe in production, missing timeouts and limits, failure modes that take the service down.\nProve what you can by tracing a concrete input through the code or with a quick reproduction.",
     "needs_input": false
    },
    {
     "id": "logic",
     "title": "Hunt logic bugs the tests miss",
     "blurb": "Wrong results on some inputs, each with a triggering case.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "audit",
      "correctness"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this repository}}\n\nFind functions that return wrong results in some cases. Focus on code that carries real logic: parsing, calculation, state transitions, non-trivial conditionals, and date, time, and money handling.\nFor each candidate:\n1. State in one sentence what it must guarantee, and check that against how callers actually use it.\n2. Hunt concrete defects: off-by-one and boundary errors, wrong or inverted comparisons, early returns that skip work, mutated shared or aliased state, overflow and precision loss, wrong defaults or fallthrough, unhandled branches.\n3. Run the edge cases that matter (empty, one, max, zero or negative, null, duplicate, unsorted, unicode, huge, malformed) and compare what the code does with what it should do.\n4. Prove each with the triggering input traced through the code, or a quick failing test in a temp directory.\nRank silent wrong results above crashes above cosmetic issues.",
     "needs_input": false
    },
    {
     "id": "error-handling",
     "title": "Audit error handling and failure paths",
     "blurb": "Swallowed errors, lost context, broken retries, missing timeouts.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "audit",
      "correctness"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this repository}}\n\nAudit what this code does when things go wrong: error handling, fallbacks, retries, timeouts, and cleanup.\n1. Swallowed failures: errors caught and ignored, logged-and-continued when they should stop, or turned into a success or default that hides the problem.\n2. Lossy propagation: errors that lose their cause, get remapped to the wrong type or status, show a misleading message, or leak internals.\n3. Crashes on bad input: unchecked indexing, casts, null access, unwrap or assert, division, parsing.\n4. Broken recovery: retries without backoff, limits, or idempotency; fallbacks that fire on the wrong condition; cleanup that skips the error path; partial failures that leave state half-written.\n5. Missing limits: external calls with no timeout, unbounded waits, and IO, network, or parsing treated as if it can't fail.\nFor each, give the correct handling: propagate with context, fail closed, validate first, or make cleanup unconditional.",
     "needs_input": false
    },
    {
     "id": "data-integrity",
     "title": "Find writes that corrupt data",
     "blurb": "Every write path that can land wrong, stale, or duplicate data.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "audit",
      "database"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this repository}}\n\nFind every way this code can persist wrong, stale, duplicated, or inconsistent data in its database, cache, files, or other state.\n1. Map every writer: request handlers, background jobs, retries, migrations, imports, cache refreshes, and admin tools. Read the schema and migrations to confirm constraints and transaction boundaries instead of assuming them.\n2. For each writer, find the input or timing that produces a bad value: partial failure without a transaction, non-idempotent retry, read-modify-write race, wrong WHERE clause or key, cache and database updated separately, a constraint the code assumes but the schema lacks.\n3. For each, propose a guard that makes the bad state impossible (transaction, unique or foreign-key constraint, idempotency key, version check, single writer), not a symptom patch.\n4. Give a read-only query or check that finds records already corrupted, so they can be repaired.",
     "needs_input": false
    },
    {
     "id": "concurrency",
     "title": "Hunt races and deadlocks",
     "blurb": "Each bug proven with an explicit interleaving.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "audit",
      "correctness"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this repository}}\n\nFind concurrency bugs: races, lost updates, deadlocks, and visibility problems. Start from what runs concurrently: threads, async tasks, workers, multiple processes or instances, and shared caches, files, or connections.\n1. List the state shared across concurrent execution and mark each piece safe or unsafe without synchronization.\n2. For each unsafe access, write the interleaving that breaks it (T1 reads X, T2 writes X, T1 writes stale X). The interleaving is the proof.\n3. Find check-then-act gaps and read-modify-write sequences that look atomic but are not, including across processes and in the database.\n4. Find deadlock hazards: locks taken in different orders, and locks held across an await, IO, or callback.\n5. Give the minimal correct fix (narrower critical section, lock ordering, atomic operation, immutable state, single owner or queue, database constraint) and flag any that costs throughput.\nState the concurrency model you assume when the code does not make it clear.",
     "needs_input": false
    },
    {
     "id": "leaks",
     "title": "Hunt leaks and unbounded growth",
     "blurb": "What never gets released, and what grows until it falls over.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "audit",
      "performance"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this repository}}\n\nFind what this code acquires and never releases, or lets grow without limit until the process degrades.\n1. Handles and connections: files, sockets, database connections, HTTP clients, and subprocesses that are released on some paths but not on errors or early returns.\n2. Tasks and threads: started and never awaited, joined, or cancelled; timers and listeners never removed.\n3. Locks and transactions: not released or rolled back on every exit path.\n4. Unbounded growth: caches, maps, queues, logs, and buffers that only grow; reads and queries with no limit; references kept after use.\n5. Lifetime mismatches: a resource that outlives, or dies before, what depends on it.\nFor each, give the sequence that reproduces the growth and the fix (scoped release, eviction bound or TTL, pagination, cancellation). Rank by how fast it bites under real load.",
     "needs_input": false
    },
    {
     "id": "api-contract",
     "title": "Pressure-test the API surface",
     "blurb": "Validation, errors, idempotency, limits, and compatibility per endpoint.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "review",
      "security"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|every HTTP, RPC, and IPC endpoint}}\n\nReview each endpoint against what it will meet in production. Find the route handlers and their request and response types.\nFor each endpoint:\n1. Inputs: validation, types, bounds, required versus optional, and what happens on missing, extra, malformed, or oversized values.\n2. Errors: bad input that returns 500 instead of 4xx, and error bodies that leak internals or say nothing useful.\n3. Semantics: idempotency on retry and double submit, partial failure mid-write, check-then-act races.\n4. Limits: pagination, payload size, timeouts, rate limits, unbounded result sets.\n5. Output: a stable, consistent shape with no internal ids, stack traces, or personal data.\n6. Compatibility: changes that break existing clients (renamed or removed fields, changed types or status codes, new required inputs) without a version or a transition.\nGroup findings by endpoint and call out the one or two that are genuinely dangerous.",
     "needs_input": false
    }
   ]
  },
  {
   "id": "security",
   "name": "Security",
   "icon": "shield",
   "blurb": "Threat-model a feature, or audit for the vulnerabilities that actually get exploited.",
   "prompts": [
    {
     "id": "sec-full",
     "title": "Full security audit of every layer",
     "blurb": "Breadth sweep of every layer, with a verdict per layer.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "security",
      "audit"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|the whole repository}}\n\nRun an adversarial security audit as a senior red-team engineer who has the source code and wants to compromise the system, either as an outsider or as a malicious user.\n1. Map the attack surface layer by layer (frontend, backend and APIs, clients, auth, storage, inter-service and IPC, dependencies, build and deploy, config and secrets), with the trust boundary at each.\n2. For each layer, hunt the classes that apply: injection, broken access control and IDOR, SSRF, path traversal, unsafe deserialization, XSS, credential exposure, privilege escalation, insecure defaults, races, business-logic abuse, supply chain.\n3. For each finding: the attacker's starting position, the exploit path with `path:line` at each step, a proof-of-concept request or input where feasible, and the hardening fix.\n4. Note code that looks risky but is safe, and why, so it isn't flagged again.\n5. End with a per-layer verdict (sound, weak, or broken) so gaps in coverage are visible.\nIgnore style and features unless they create risk.",
     "needs_input": false
    },
    {
     "id": "sec-web",
     "title": "Hunt injection, XSS, and SSRF",
     "blurb": "Input-handling vulnerabilities, each with a payload.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "security",
      "audit"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this repository}}\n\nFind exploitable input-handling vulnerabilities. For each, show the concrete attack (the payload and the path it takes), not just a label.\n1. Injection: SQL and NoSQL, shell commands, path traversal, templates, header or CRLF, regex DoS. Anywhere untrusted input reaches an interpreter, query, path, or shell without parameterization or escaping.\n2. Output: XSS through HTML, attributes, JS, URLs, and markdown rendering; dangerous sinks (innerHTML, eval, v-html, dangerouslySetInnerHTML); missing or weak CSP.\n3. Server-side fetches: SSRF through user-supplied URLs, redirects, webhooks, and imports, including internal addresses and cloud metadata endpoints.\n4. Parsing and files: unsafe deserialization, XML external entities, archive extraction (zip slip), uploads (type, size, storage location, and what they are served as).\n5. Redirects and binding: open redirects, and request bodies bound straight to models (mass assignment).\n6. Browser-facing config: CSRF protection on state-changing requests, cookie flags (Secure, HttpOnly, SameSite), CORS that reflects any origin or allows credentials too broadly, and missing security headers.\nFor each, give the fix: parameterize, encode on output, allowlist, or validate at the boundary.",
     "needs_input": false
    },
    {
     "id": "sec-authz",
     "title": "Audit access control as a malicious user",
     "blurb": "Role × action matrix: IDOR, escalation, stale access.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "security",
      "audit"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|every route, job, webhook, and IPC entry point}}\n\nAudit access control from the position of a legitimate, logged-in user, or a low-privileged staff account, who wants more than they are allowed.\n1. Identities: anonymous, each role, admin, service accounts, API keys, device tokens, share links; how each authenticates and how long its credentials live.\n2. Actions: every entry point that reads, changes, or deletes data, with the exact check it performs (who is calling, do they own it, which role). Quote the check, or note that there isn't one.\n3. Build the role × action matrix and test each cell. Horizontal: reach another user's objects by changing an id, path, token, or nested reference, including through bulk, export, search, preview, and thumbnail paths. Vertical: reach admin behavior, set your own role or flags, or use a route that forgets the check. Lifecycle: revoked, expired, deleted, demoted, or logged-out identities that keep access through sessions, tokens, share links, or other devices.\n4. Check the checks: decisions based on client-supplied data, checks after the side effect, time-of-check/time-of-use races, roles read from a stale cache.\n5. Authentication flows: login, reset, and signup (account enumeration, brute-force limits, token entropy and expiry, session fixation).\n6. Insider traces: what an admin or server operator could do without leaving an audit trail.\nFor each finding: the exact request a user would send, the `path:line` that should have stopped it, and the fix.",
     "needs_input": false
    },
    {
     "id": "sec-chains",
     "title": "Find the shortest path to full compromise",
     "blurb": "Work backwards from the attacker's prize.",
     "modes": [
      "audit"
     ],
     "tags": [
      "security",
      "audit"
     ],
     "needs": "project",
     "body": "Act as an attacker who has the full source code and one goal: compromise this system as cheaply as possible. Find complete attack chains, not isolated bugs. Read the code, config, and deployment files.\n1. Prizes: admin or root, another user's data or account, code execution on any server or client, secrets and keys, and persistence that survives a fix or redeploy.\n2. For each prize, work backwards: the last step that grants it, what that step needs, and where an attacker gets that, until you reach something an anonymous visitor or a fresh free account can do.\n3. Look hardest at seams between components, where each side assumes the other validated the input.\n4. For each chain: starting position → each step with the `path:line` that enables it → what the attacker holds at the end. Mark each link confirmed or assumed, and say what would make an assumption true.\n5. Rank chains by impact × likelihood ÷ attacker effort, cheapest critical first.\n6. Give the single cheapest fix that breaks each chain, then the defense-in-depth fixes at other links.\nIf a prize is out of reach, say what stops the attacker and how confident you are. No chains that start from an already compromised server.",
     "needs_input": false
    },
    {
     "id": "sec-clients",
     "title": "Attack the clients, tokens, and crypto",
     "blurb": "Treat every client as hostile and every server as suspect.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "security",
      "audit"
     ],
     "needs": "project",
     "body": "Audit the client side of this system and its cryptography as an attacker. Assume every client (browser, desktop, mobile, CLI) can be modified by its user, and that a malicious website, a local process, the network, or a compromised server will try to abuse it.\n1. Trust placement: anything the server trusts only because a client enforced it (hidden UI, client-side validation, client-computed prices, quotas, or permissions). Show the raw request that skips it.\n2. Credentials: where tokens, keys, and session data live on the device, who else can read them, whether they leak into logs, crash reports, URLs, or caches, and whether TLS is enforced and verified.\n3. Local attack surface: IPC, local HTTP servers, custom URL schemes and deep links, preload or bridge APIs, file associations, command-line arguments. Can a web page or another process trigger privileged actions through them?\n4. Browser code: DOM XSS, unsafe HTML rendering, postMessage without origin checks, whether one XSS can steal keys or tokens, and CSP gaps.\n5. Updates and downloads: is every binary, script, and update verified (signature or pinned hash), fetched over TLS, and protected from downgrade? Could a server or network attacker push code to clients?\n6. Cryptography: algorithms and modes, key-derivation parameters, nonce or IV reuse, randomness, authenticated encryption, key wrapping and rotation, and anything an end-to-end or zero-knowledge claim depends on that the server could see or tamper with (filenames, sizes, metadata, the JavaScript it serves).\nFor each finding, name the attacker's position: malicious user, web page, local process, network, or server.",
     "needs_input": false
    },
    {
     "id": "sec-pipeline",
     "title": "Audit the build, CI, and deploy chain",
     "blurb": "Where code or secrets can be tampered with before production.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "security",
      "audit"
     ],
     "needs": "project",
     "body": "Audit the path from commit to running production for places an attacker could inject code, steal secrets, or take over a host. Assume they may control a dependency, a pull request, a CI runner, a mirror, or a low-privileged server account. Read CI workflows; build, release, deploy, backup, and install scripts; service units; container files; and server config in the repo.\n1. CI/CD: triggers that run untrusted code with secrets (pull_request_target, forks, comment commands), script injection from branch names or titles, overbroad tokens, unpinned third-party actions, secrets in logs or artifacts.\n2. Builds: install-time scripts that run in CI, tools and base images fetched without a checksum or digest, and whether the artifact can be reproduced from the commit.\n3. Releases: are binaries, installers, and update manifests signed, and where do the keys live? Could a compromised release or mirror reach users?\n4. Deploy scripts: running as root, unquoted variables, predictable temp files, copy or rsync steps that follow symlinks or delete the wrong path, rollback and backup integrity, anything that publishes secrets or private data.\n5. Runtime: services with more privilege than they need, file permissions, exposed debug or admin endpoints, network bindings, TLS and proxy settings, dev-only switches reachable in production.\n6. Secrets lifecycle: where each secret is created, stored, read, rotated, and revoked, and who can read it.\nFor each finding, give the attacker's starting position and the steps to code execution or secret theft. State any assumption about infrastructure the repo does not show.",
     "needs_input": false
    },
    {
     "id": "sec-deps",
     "title": "Audit dependencies and leaked secrets",
     "blurb": "Advisories that are actually reachable, and secrets in code or history.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "security"
     ],
     "needs": "project",
     "body": "Audit this project's third-party dependencies and the secrets in its repository.\n1. Dependencies: run the ecosystem's audit tool if it is available (npm audit, pip-audit, cargo audit, govulncheck, bundler-audit) and read the manifest and lockfile. Flag known advisories, unmaintained or badly outdated packages, and typosquat-looking names. Separate direct from transitive, and say whether the vulnerable code path is reachable from our usage.\n2. Pinning: unpinned or unlocked versions, install-time scripts, dependencies fetched from URLs or git without a hash.\n3. Secrets: credentials, keys, or tokens in code, config, tests, fixtures, or git history (`git log -p -S` for likely patterns), and secrets that are logged, sent to clients, or baked into build artifacts. For any exposed secret, rotate first, then remove.\nWhere exploitability depends on context the repo does not show, state the assumption and rate the risk under it.",
     "needs_input": false
    },
    {
     "id": "sec-threat",
     "title": "Threat-model a feature",
     "blurb": "How it gets abused, before it ships.",
     "modes": [
      "read",
      "plan"
     ],
     "tags": [
      "security",
      "architecture"
     ],
     "needs": "any",
     "body": "Feature: {{Feature, or the path where it lives}}\n\nThreat-model this feature before it ships. If it exists in the code, read it. If it is still an idea and the description doesn't cover its data and trust boundaries, ask me about them first.\n1. Assets: what is at risk (data, money, accounts, compute, reputation) and the attackers who realistically want it.\n2. Trust boundaries and entry points: every place untrusted input enters, and every privileged action it can reach.\n3. Abuse cases for each entry point, written as an attacker's steps: injection, broken authorization and IDOR, SSRF, deserialization, missing rate limits, secrets exposure, and business-logic abuse (replay, races, negative amounts, skipped workflow steps).\n4. Rate each by impact × likelihood, and say which one or two are genuinely scary versus theoretical.\n5. For the top risks: the specific mitigation, where in the flow it belongs (validate and authorize at the boundary), and the test that proves it.\nBe specific to this feature; no generic checklist.",
     "needs_input": true
    }
   ]
  },
  {
   "id": "perf",
   "name": "Performance",
   "icon": "gauge",
   "blurb": "Find the real bottleneck with evidence, then make a change you can measure.",
   "prompts": [
    {
     "id": "perf-profile",
     "title": "Diagnose a specific slowdown",
     "blurb": "You know what is slow; measure it and fix the dominant cost.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "performance",
      "debugging"
     ],
     "needs": "project",
     "body": "Slow: {{What is slow, how slow, and since when}}\n\nFind what actually makes this slow. Do not optimize from reading alone.\n1. Measure first. Run the profiler, benchmark, trace, or timing breakdown yourself if the project can run here. Otherwise give me the exact commands and stop until I paste the results.\n2. From the numbers, name the dominant cost and why it is expensive: algorithmic complexity, IO and round trips, contention, serialization, allocation.\n3. Propose fixes for that cost, ordered by expected win per unit of effort and risk, with an estimate for each.\n4. Name the tempting micro-optimizations the profile says aren't worth it.\n5. Define the proof: the same measurement before and after, plus a check that correctness and other metrics didn't regress.",
     "needs_input": true
    },
    {
     "id": "perf-latency",
     "title": "Audit request latency: IO and caching",
     "blurb": "Serial calls, missing reuse, and work done on the request that needn't be.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "performance",
      "audit"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|the request and job paths in this repository}}\n\nFind where requests wait on IO they don't need to wait on. Trace the main request and job paths from entry to response.\n1. Serial IO: independent network, database, or file calls made one after another that could run concurrently or be batched.\n2. Connection reuse: clients created per request, no keep-alive or pooling, repeated TLS handshakes or DNS lookups.\n3. Caching: expensive results recomputed or refetched on every request with no cache, and caches with no invalidation story.\n4. Work on the hot path: email, webhooks, thumbnails, logging, or analytics done before responding when it could happen in the background.\n5. Blocking the loop: synchronous IO or CPU-heavy work inside async handlers or the UI thread.\nFor each: the path, an estimate of the latency it adds, and the fix.",
     "needs_input": false
    },
    {
     "id": "perf-scale",
     "title": "Audit what breaks at 10x load",
     "blurb": "Capacity limits before traffic finds them.",
     "modes": [
      "audit"
     ],
     "tags": [
      "performance",
      "audit"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this system}}\n\nFind what fails first if traffic, data, or users grow tenfold.\n1. Work that grows with total data instead of the request: full-table scans, loading every row or file to filter in code, per-request loops over all users or items.\n2. Single points of serialization: global locks, one worker or thread doing all of something, a single queue consumer, a hot row everyone writes.\n3. Fixed limits: connection pools, file descriptors, memory per request, disk, rate limits of third-party APIs, and what happens when each runs out.\n4. State that blocks scaling out: in-process caches, sessions, or schedulers that break with two instances.\n5. Backpressure: queues and buffers with no bound, and no shedding or rejection when overloaded.\nFor each: the load at which it breaks (estimated), the symptom users would see, and the fix.\nSeverity scale: rank by how soon it breaks under realistic growth.",
     "needs_input": false
    },
    {
     "id": "perf-db",
     "title": "Find slow queries and N+1s",
     "blurb": "Query count, scans, over-fetching, and justified indexes.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "performance",
      "database"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this repository}}\n\nFind database access that is slower than it needs to be. Read the queries, ORM calls, the code that issues them, and the schema and indexes.\n1. N+1: queries inside loops, and per-record lazy loads that should be one batched or joined query. Quote the code.\n2. Expensive queries: what the engine has to do for each (which index it can use, where it scans or sorts) and the exact EXPLAIN, or EXPLAIN ANALYZE on a safe copy, that confirms it.\n3. Over-fetching: columns or rows fetched and thrown away, large blobs read when only metadata is needed, missing pagination, unbounded result sets.\n4. Writes: per-row inserts that could be batched, long transactions holding locks, hot rows rewritten often.\n5. Fixes in order of leverage: restructure the query, add a specific index (columns, order, and why), batch, or cache, with the write and storage cost of each index.\nJustify every index against the queries that will use it.",
     "needs_input": false
    },
    {
     "id": "perf-memory",
     "title": "Cut memory use and allocation churn",
     "blurb": "Footprint and per-iteration churn on the hot path, measured.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "performance"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this repository}}\n\nFind where this code uses more memory, or allocates more, than it should: high RSS, GC pressure, or a hot loop slowed by churn.\n1. Find the hot paths and the large or long-lived data structures first. Measure with a heap or allocation profiler if you can run one.\n2. Separate allocated-once from allocated per call or iteration; the latter is where the pressure comes from. Quote those lines.\n3. Look for copies that could be views or borrows, repeated buffer growth, intermediate collections that could stream, whole files or responses read into memory, duplicated data that could be shared, and containers not pre-sized.\n4. Propose fixes ordered by impact, flag any that cost readability, and say when the workload makes one not worth it. Give the before/after measurement.",
     "needs_input": false
    },
    {
     "id": "perf-frontend",
     "title": "Speed up page load and rendering",
     "blurb": "Bundle, render-blocking, jank, chatty requests.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "performance",
      "frontend"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|the web UI}}\n\nFind what makes this slow to load or janky to use.\n1. Load: bundle and asset sizes, render-blocking scripts and CSS, fonts, images without dimensions or modern formats, missing caching or compression headers, request waterfalls. Measure with Lighthouse or the build's size report if you can run them.\n2. Rendering: layout thrashing (interleaved reads and writes), work on every keystroke or scroll, long tasks over 50 ms, large lists rendered without virtualization, unnecessary re-renders.\n3. Network: duplicate or chatty requests, no deduping or cancellation, data fetched and thrown away, polling that could be a push.\n4. Perceived speed: missing skeletons or optimistic updates where waiting can't be avoided, and layout shift as content arrives.\nFor each: the measured or estimated cost, the fix, and how to confirm it.",
     "needs_input": false
    }
   ]
  },
  {
   "id": "frontend",
   "name": "Frontend & UI",
   "icon": "layout",
   "blurb": "Layout, state, accessibility, responsiveness, and polish, diagnosed from first principles.",
   "prompts": [
    {
     "id": "ui-layout",
     "title": "Audit layout and responsive breakage",
     "blurb": "CSS that breaks with content, width, touch, or browser engine.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "frontend"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|the UI}}\nKnown symptom: {{Symptom|none, audit broadly}}\n\nFind layout that misaligns, overflows, collapses, or sizes wrong, now or as soon as content, width, or browser changes. Start from the symptom if there is one. Diagnose from first principles, never by trial and error.\n1. Mechanism: name the layout system in charge (flow, flex, grid, positioned), what the box model is doing (min-content sizing, margin collapse, stacking contexts), and the rule or missing rule behind each break, e.g. \"the flex item has min-width: auto, so it won't shrink below its content.\"\n2. Content: long words and names, empty states, many items, translated text, and user-sized fonts.\n3. Width and touch: the width ranges where it breaks and why, hover-only interactions, 100vh on mobile, the on-screen keyboard covering inputs, safe-area insets.\n4. Engines: Safari and Firefox differences in sticky positioning, gap, scrolling, and form controls, for the browsers this project supports (check its browserslist or docs).\n5. The smallest fix to each cause, preferring intrinsic sizing, clamp(), minmax(), and container queries over more breakpoints. No !important, magic numbers, or z-index escalation without a reason. Note side effects on siblings, breakpoints, and themes.\nIf you can run the app in a headless browser, check real widths; otherwise say that you reasoned from the code.",
     "needs_input": false
    },
    {
     "id": "ui-state",
     "title": "Audit UI state and re-render bugs",
     "blurb": "Stale values, loops, async races, missing cleanup.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "frontend"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|the UI}}\n\nFind stale values, missed updates, render loops, unexpected resets, and double-fires.\n1. Trace each important piece of state: where it is declared, read, and written, and the path from event to state change to render to DOM.\n2. Look for: state mutated in place; stale closures; derived state stored instead of computed; effects with wrong dependencies; unstable keys causing remounts; new objects or arrays on every render triggering loops; async races (an older request finishing last); listeners and timers never cleaned up.\n3. For each, point to the line holding the wrong assumption and explain the timing that produces the symptom.\n4. Give the minimal fix, preferring derived values over stored copies and one source of truth over syncing.\nState the framework version or update model you assume when it matters.",
     "needs_input": false
    },
    {
     "id": "ui-a11y",
     "title": "Accessibility audit",
     "blurb": "Keyboard and screen-reader blockers, with exact markup fixes.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "frontend",
      "accessibility"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|the UI}}\n\nAudit this for accessibility the way keyboard and screen-reader users actually use it, not as a generic checklist.\n1. Semantics: div or span impostors for buttons, links, and inputs (name the native element); wrong or redundant ARIA; heading structure; landmarks.\n2. Keyboard: everything reachable and operable without a mouse; logical tab order; visible focus; Enter, Space, Escape, and arrow keys where expected; focus moved into and back out of dialogs, menus, and popovers.\n3. Names and announcements: an accessible name on every control; state changes (expanded, selected, loading, errors, new messages) announced; decorative icons hidden and meaningful ones labeled.\n4. Forms: labels tied to inputs, errors linked programmatically, required and invalid states not shown by color alone.\n5. Visual: contrast below WCAG AA, color-only signals, text that breaks at 200% zoom, motion without prefers-reduced-motion.\nFor each: the WCAG criterion in plain words and the exact markup or attribute change. Severity scale: Blocker (unusable by keyboard or screen reader) / Barrier / Polish.",
     "needs_input": false
    },
    {
     "id": "ui-failure",
     "title": "Audit loading, empty, and failure states",
     "blurb": "What users see when the network is slow, a request fails, or there is no data.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "frontend",
      "audit"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|the UI}}\n\nAudit what the interface does when things are not on the happy path. For each screen and action that loads or saves data:\n1. Loading: is there feedback, does it ever end (spinners that spin forever after an error), and does the layout jump when data arrives?\n2. Failure: is the error shown at all, is it in words a user can act on, can they retry, and is their input kept rather than wiped?\n3. Empty: first-run and no-results states that explain what to do instead of a blank area.\n4. Slow and offline: double submits while waiting, stale data shown as current, actions that silently fail offline, optimistic updates never rolled back on failure.\n5. Partial: one failed widget taking the whole page down, or a list that shows half its items without saying so.\nFor each: the screen or component, how to trigger it (throttle, block a request, empty account), what the user sees now, and what they should see.",
     "needs_input": false
    },
    {
     "id": "ui-forms",
     "title": "Audit forms and user input",
     "blurb": "Validation, errors, submit behavior, and input types that fight users.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "frontend",
      "audit"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|every form and input in the UI}}\n\nAudit each form the way a hurried user on a phone would fill it in.\n1. Validation: rules that differ between client and server, errors shown only after submit or only as a toast, messages that don't say how to fix it, valid input rejected (names with apostrophes, plus-addressed emails, pasted values with spaces).\n2. Submit: double submit, Enter key behavior, disabled buttons with no reason given, lost input on error or navigation.\n3. Input types: missing type, inputmode, and autocomplete attributes (so phones show the wrong keyboard and password managers can't fill), fields that block paste.\n4. Defaults and limits: maxlength that truncates silently, numbers and dates parsed in the wrong locale or time zone, required fields not marked.\nFor each: the form and field, the input that goes wrong, and the exact fix.",
     "needs_input": false
    },
    {
     "id": "ui-polish",
     "title": "Make the UI consistent and polished",
     "blurb": "Tokens, states, themes, and copy that drift.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "frontend",
      "design"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|the UI}}\n\nReview this for the visual and interaction inconsistencies that make it feel unfinished.\n1. Tokens: hard-coded colors, spacing, radii, shadows, and font sizes that bypass the design system or theme variables, and near-duplicates that should be one value.\n2. Type and spacing: inconsistent scales, line lengths, alignment, and density between similar screens.\n3. Interaction states: missing or inconsistent hover, focus, active, and disabled states on each interactive component.\n4. Themes: anything that breaks in dark mode, light mode, or other themes (unreadable text, invisible borders, hard-coded backgrounds).\n5. Copy: inconsistent labels, casing, and terms for the same action.\nFor each: where it is, what the consistent version is, and the token or component to use. Group fixes that one shared change would resolve.",
     "needs_input": false
    }
   ]
  },
  {
   "id": "testing",
   "name": "Testing",
   "icon": "flask",
   "blurb": "Plan tests, write the cases that catch bugs, find gaps and flakes.",
   "prompts": [
    {
     "id": "test-plan",
     "title": "Build a test plan",
     "blurb": "Prioritized cases, plus the five to test if nothing else.",
     "modes": [
      "read"
     ],
     "tags": [
      "testing"
     ],
     "needs": "project",
     "body": "Feature or change: {{Feature or change|the current uncommitted change}}\n\nBuild a prioritized test plan from the code as it actually is. If it isn't built yet, plan from the description and state what you assumed.\n1. Requirements: the explicit ones and the implicit ones the code relies on (auth, permissions, limits, errors, concurrency, idempotency). Flag any that are ambiguous and need a decision.\n2. Cases by category (happy path, boundaries, invalid input, failure paths, permission and state variations, integrations), each as precondition → action → expected result.\n3. The nasty, realistic cases a rushed developer misses: empty and huge inputs, concurrent actions, partial failure, retries, time zones, unicode, network loss mid-operation.\n4. Which cases to automate and at what level (unit, integration, end to end), and which are one-off manual checks.\n5. \"If you test only five things\": the shortlist, ranked by risk × likelihood.",
     "needs_input": false
    },
    {
     "id": "test-edges",
     "title": "Write tests for the cases that break things",
     "blurb": "Edge-case tests in your framework; real bugs reported, not patched.",
     "modes": [
      "act",
      "plan"
     ],
     "tags": [
      "testing"
     ],
     "needs": "project",
     "body": "Target: {{Target|the most logic-heavy, least-tested code you find}}\n\nWrite tests aimed at the inputs most likely to break this code, in the project's existing test framework and style.\n1. State the contract under test: inputs, outputs, side effects, invariants. Where the intended behavior is unclear, list it as a question instead of guessing an assertion.\n2. Derive cases from the input space, not the implementation: 0, 1, max, max+1; empty, null, missing; negative; duplicates; unsorted; huge; malformed; valid but unusual (unicode, whitespace).\n3. Cover behavior: error paths, idempotency, ordering, and at least one test that fails if a plausible future \"optimization\" breaks the contract.\n4. One behavior per test, named for the case (returns_empty_when_input_is_empty). Deterministic: no dependence on real time, network, or ordering.\n5. Run them. If a test exposes a real bug, do not change product code: mark that test as failing (or expected-failure, per project convention) and report it.",
     "needs_input": false
    },
    {
     "id": "test-gaps",
     "title": "Find coverage gaps that matter",
     "blurb": "Untested risk and weak tests posing as coverage.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "testing",
      "audit"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|the whole test suite}}\n\nThe tests pass, but passing isn't the same as tested. Find where a real bug could hide without any test noticing. Read the code and its tests, and generate a coverage report if the project supports it.\n1. Map what the tests actually assert versus what they merely execute. Find branches, error paths, and conditions with no meaningful assertion behind them.\n2. High-risk untested paths: error handling, fallbacks, permission checks, edge conditions, the \"can't happen\" branches.\n3. Weak tests posing as coverage: no real assertion, mocking the very thing under test, or still passing if the logic were broken. Name them.\n4. Seams where unit tests pass but integration would fail, and where an integration test is the right tool.\n5. The specific tests to add, ranked by the risk they retire, each with the exact scenario to assert.\nChase risk, not a coverage percentage.",
     "needs_input": false
    },
    {
     "id": "test-flaky",
     "title": "Hunt flaky tests",
     "blurb": "Find nondeterminism and fix it without retries or sleeps.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "testing"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this repository}}\n\nFind tests that pass or fail without a code change, and why.\n1. If you can, run the suspect suites several times (and in random order or in parallel if supported) and note which fail intermittently.\n2. Read for flake sources: real time and sleeps, time zones and dates, random data without a seed, test order and shared state, unawaited async work, real network or filesystem, port collisions, resource limits, global mocks never restored.\n3. For each flaky test: the source, the evidence, and the deterministic fix (fake clock, seeded data, isolated fixture, an explicit wait on a condition).\nNever fix a flake by adding retries, longer sleeps, or skipping it.",
     "needs_input": false
    }
   ]
  },
  {
   "id": "ops",
   "name": "Ship & operate",
   "icon": "rocket",
   "blurb": "Check a change before release, debug production, make failures visible.",
   "prompts": [
    {
     "id": "preflight",
     "title": "Pre-release check for this change",
     "blurb": "Go / no-go with deploy and rollback steps.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "release"
     ],
     "needs": "project",
     "body": "Change: {{Change|uncommitted work, or this branch against its base}}\n\nCheck whether this change is safe to ship.\n1. Completeness: anything half-done, such as TODOs, debug code, commented-out blocks, stray files, or hard-coded test values.\n2. Verification: run the project's tests, lint, type check, and build, and report the results.\n3. Compatibility: schema or data migrations (additive? reversible? deploy before or after migrating?), config or environment changes, and API or file-format changes that existing clients or stored data depend on.\n4. Operations: new dependencies, permissions, ports, or services; logging on the new paths; how to roll back.\n5. Secrets and data: nothing sensitive in the diff, logs, or fixtures.\nSeverity scale: Blocker / Should fix / Note. End with go or no-go and the exact steps to deploy and to roll back.",
     "needs_input": false
    },
    {
     "id": "incident",
     "title": "Investigate a production incident",
     "blurb": "Evidence first, mitigate separately, then root cause.",
     "modes": [
      "read",
      "act"
     ],
     "tags": [
      "debugging",
      "ops"
     ],
     "needs": "project",
     "body": "Symptoms: {{What is happening: errors, alerts, user reports}}\nSince when: {{When it started, and what changed around then|unknown}}\n\nInvestigate this like an on-call engineer: stabilize first, then find the root cause.\n1. Gather evidence before theorizing: logs, metrics, recent deploys and commits (`git log` around the start time), and config, dependency, or infrastructure changes. Quote what you find.\n2. List the hypotheses that fit the evidence, and rule each in or out with a specific check.\n3. If users are affected right now, propose the fastest safe mitigation (rollback, feature flag, restart, scale), separate from the real fix, with its risk. Do not run it without my OK.\n4. Root cause: the chain from trigger to symptom, with `path:line`.\n5. The fix, a test or alert that would have caught it, and a short timeline for the write-up.",
     "needs_input": true
    },
    {
     "id": "observability",
     "title": "Audit logging and observability",
     "blurb": "Could this be debugged at 3 a.m. from its signals alone?",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "ops"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this system}}\n\nCheck whether this could be debugged at 3 a.m. from its logs, metrics, and errors alone.\n1. Failure visibility: errors swallowed without a log, logs without the context to act on (ids, inputs, the operation), one failure logged five times.\n2. Log hygiene: secrets, tokens, or personal data in logs; unbounded or noisy logging on hot paths; inconsistent levels and formats.\n3. Correlation: request or job ids carried across services, threads, and background work.\n4. Metrics and health: latency, error rate, and saturation on the key paths; health checks that test real dependencies; alerts that fire on symptoms users feel.\n5. For the three most likely production failures in this system, say whether you could diagnose each from the current signals, and what to add if not.",
     "needs_input": false
    },
    {
     "id": "backups",
     "title": "Audit backups and recovery",
     "blurb": "Could you actually restore, and how much would you lose?",
     "modes": [
      "audit"
     ],
     "tags": [
      "ops",
      "audit",
      "database"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|every datastore this system writes to}}\n\nFind out whether this system's data survives a bad deploy, a deleted table, a dead disk, or a compromised server.\n1. Inventory every place durable data lives (databases, object storage, uploaded files, local disks, queues, third-party services) and whether each is backed up at all.\n2. For each backup: how it is made, how often (the most data you could lose), where it is stored (same host or account as the original?), whether it is encrypted, and how long it is kept.\n3. Restore: is there a written, tested restore procedure, how long would it take, and does it restore a consistent state across stores (database plus files)?\n4. Destructive paths: migrations, cleanup jobs, and admin actions that delete data with no soft delete or undo.\nEnd with the worst realistic loss scenario and the cheapest change that shrinks it.",
     "needs_input": false
    },
    {
     "id": "config-audit",
     "title": "Audit config and environments",
     "blurb": "Unsafe defaults, missing validation, and dev/prod drift.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "ops",
      "audit"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|this repository}}\n\nAudit how this system is configured across environments.\n1. Loading: settings read in many places instead of one, missing values that only fail deep in a request instead of at startup, wrong types accepted silently.\n2. Defaults: defaults that are unsafe if production forgets to override them (debug on, permissive CORS, test keys, open signups, verbose errors).\n3. Separation: secrets mixed into settings files, settings hard-coded in code, and an example file (.env.example or similar) that is out of date with what the code reads.\n4. Drift: options that differ between dev, staging, and production in ways that would hide a bug, and feature flags nobody can turn off.\nFor each: where it is read, the failure it allows, and the fix.",
     "needs_input": false
    }
   ]
  },
  {
   "id": "design",
   "name": "Design & decide",
   "icon": "layers",
   "blurb": "Specs, design docs, honest trade-offs, safe migrations, a sharp sounding board.",
   "prompts": [
    {
     "id": "spec",
     "title": "Turn a vague idea into a spec",
     "blurb": "Interrogate the idea, then a buildable spec and first slice.",
     "modes": [
      "talk"
     ],
     "tags": [
      "planning"
     ],
     "needs": "any",
     "body": "Idea: {{The rough idea}}\n\nHelp me turn this into a buildable spec before any code is written. Interrogate it before writing anything, and ground it in this codebase where it touches it.\n1. Play it back in one paragraph, with the assumptions you had to make. Mark the ones that change the build.\n2. Ask the five to eight questions that most change scope, effort, or design (who it's for, the core job, what's out of scope, hard constraints, what done looks like). Then wait for my answers.\n3. Then draft the spec: problem, users, core flows, functional and non-functional requirements, non-goals, acceptance criteria.\n4. Flag the risky, ambiguous, or expensive parts we could cut from a first version.\n5. Propose the smallest first slice that delivers the core value, and what to defer.\nPush back if the idea is bigger than it needs to be.",
     "needs_input": true
    },
    {
     "id": "rfc",
     "title": "Draft a design doc",
     "blurb": "Goals, design, real alternatives, risks, open questions.",
     "modes": [
      "talk",
      "plan"
     ],
     "tags": [
      "architecture",
      "planning"
     ],
     "needs": "any",
     "body": "Problem: {{The problem to solve}}\nConstraints: {{Scale, latency, deadline, must-nots|ask me}}\n\nHelp me write a design doc that reviewers can poke holes in. Ground it in the existing code where it touches this codebase. If my input is too thin, ask the few sharpest questions first instead of filling gaps.\n1. Problem, goals, explicit non-goals, and success criteria.\n2. Constraints and assumptions, marking the load-bearing ones (if they're wrong, the design changes).\n3. Proposed design: components, data flow, interfaces, and contracts, concrete enough to build from.\n4. At least two real alternatives, and why they lost.\n5. Trade-offs, failure modes, operational cost, and how we observe it and roll back. Name the riskiest part.\n6. Open questions, each with who or what decides it.\nFlag anything over-engineered for the stated scale.",
     "needs_input": true
    },
    {
     "id": "tradeoffs",
     "title": "Compare approaches and pick one",
     "blurb": "An honest comparison that ends in a recommendation.",
     "modes": [
      "read",
      "talk"
     ],
     "tags": [
      "architecture",
      "planning"
     ],
     "needs": "any",
     "body": "Decision: {{What you are deciding}}\nOptions: {{The candidates|propose the realistic ones}}\nWhat matters most: {{Priorities|correctness, simplicity, operability, cost}}\n\nGive me an honest comparison that ends in a recommendation, not \"it depends\". Check how each option fits this codebase where relevant, and search the web for current facts when an option's maturity or limits matter.\n1. Restate each option neutrally. Point out any I framed unfairly, and add a clearly better option if there is one.\n2. Compare them on the dimensions that matter (mine plus any important one I missed), with a concrete reason for each, not a score.\n3. Say what each is genuinely better and worse at, and when the ranking would flip.\n4. Name the two or three deciding factors.\n5. Recommend one, state the assumptions it rests on, and the early signal that we chose wrong.",
     "needs_input": true
    },
    {
     "id": "arch-audit",
     "title": "Audit the architecture and its boundaries",
     "blurb": "Coupling, layering violations, and where the design fights the requirements.",
     "modes": [
      "audit"
     ],
     "tags": [
      "architecture",
      "audit"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|the whole system}}\n\nAudit the structure of this system, not individual bugs.\n1. Map the intended architecture (layers, modules, services, and what may depend on what) from the code and any docs, then the actual dependency graph.\n2. Violations: layers reaching past each other (UI code issuing SQL, domain code importing web frameworks), circular imports, modules that know another's internals.\n3. Coupling: changes that always have to touch the same several places, shared mutable globals, one database table or object everything reads and writes.\n4. Fit: places where the design fights current requirements, such as synchronous where it needs to be async, one tenant where it needs many, or abstractions with one implementation that only add indirection.\n5. For each issue: the evidence, what it costs today (bugs, slow changes), and the smallest structural change that would fix it.\nSeverity scale: rank by ongoing cost to change the system.",
     "needs_input": false
    },
    {
     "id": "migration",
     "title": "Plan a zero-downtime migration",
     "blurb": "Expand/contract steps, each verifiable and reversible.",
     "modes": [
      "plan"
     ],
     "tags": [
      "architecture",
      "database"
     ],
     "needs": "project",
     "body": "From: {{Current state}}\nTo: {{Target state}}\nConstraints: {{Traffic, data volume, downtime budget, dependents|read what you can from the code and ask about the rest}}\n\nPlan this production change so it runs without downtime or data loss and can be reversed. Read the affected code and schema.\n1. The invariant that must hold throughout (no lost writes, no broken reads) and the explicit rollback trigger.\n2. Expand/contract steps: add the new path beside the old, dual-write or shadow-read as needed, backfill, switch reads, remove the old path. Each step deploys on its own and is safe to pause.\n3. For each step: how to verify before moving on, how to roll back, and whether rollback still works after the next step.\n4. Data: a batched, idempotent, resumable backfill; handling writes that land mid-migration; a reconciliation check.\n5. The irreversible point (if any), the riskiest step, and what to watch during cutover.",
     "needs_input": true
    },
    {
     "id": "rubber-duck",
     "title": "Rubber-duck a problem",
     "blurb": "Pushback and one sharp question at a time.",
     "modes": [
      "talk"
     ],
     "tags": [
      "planning"
     ],
     "needs": "any",
     "body": "Problem: {{What you are stuck on}}\nTried so far: {{What you tried, and your current theory|nothing yet}}\n\nBe my pair-programming partner. I want pushback and good questions, not an immediate answer. Read the relevant code so your questions are grounded.\n1. Reflect the problem and my approach back, and name any assumption I am treating as fact without having verified it.\n2. Ask one focused question at a time, aimed at the part of my reasoning most likely to be wrong.\n3. If I'm heading into a dead end, say so directly and why.\n4. If I ask for the answer, give it with the reasoning, and point out where I could have got there myself.\n5. Every few exchanges, summarize what we've established and what's still open.",
     "needs_input": true
    }
   ]
  },
  {
   "id": "docs",
   "name": "Understand & document",
   "icon": "book",
   "blurb": "Get oriented fast, understand tricky code, write docs and history people can use.",
   "prompts": [
    {
     "id": "onboard",
     "title": "Map an unfamiliar codebase",
     "blurb": "Entry points, the 5–10 files to read first, and the danger zones.",
     "modes": [
      "read"
     ],
     "tags": [
      "documentation"
     ],
     "needs": "project",
     "body": "Task I have in mind: {{Task|none yet}}\n\nOrient me in this repository well enough to make a change confidently. Read the structure, entry points, build and config files, and docs.\n1. Big picture: what the system does, its main components, and how a request or job flows through it end to end, starting from the entry points.\n2. The map: which directories own which responsibility, the important boundaries, and the 5–10 files to read first, in order, with why.\n3. How to work here: how to install, run, test, and lint (from the project's own files), and the conventions to follow (errors, config, data access, naming, tests).\n4. Danger zones: fragile, heavily depended-on, or surprising areas where a careless change does the most damage.\n5. If I named a task: where that logic most likely lives, and the path to trace to confirm it.\nMark what you inferred rather than confirmed, and what to open to be sure.",
     "needs_input": false
    },
    {
     "id": "explain",
     "title": "Explain how this code works",
     "blurb": "Purpose, shape, the non-obvious parts, and what's load-bearing.",
     "modes": [
      "read"
     ],
     "tags": [
      "documentation"
     ],
     "needs": "project",
     "body": "Explain: {{File, function, or flow|the most central part of this codebase (say why you chose it)}}\n\nExplain this to a competent engineer who is new to this code, so I understand why it's shaped this way, not just what it does.\n1. Purpose in one or two sentences, and where it sits: who calls it and what it calls.\n2. The shape: the main pieces, and a short step-by-step of the primary path.\n3. The non-obvious parts: tricks, workarounds, implicit assumptions, ordering dependencies, subtle edge cases, and the why behind each.\n4. The contract: inputs, outputs, side effects, errors, and when it misbehaves.\n5. Before changing it: what's safe to touch and what's load-bearing.\nPut anything that looks like a bug under \"possible issues\", separate from the explanation.",
     "needs_input": false
    },
    {
     "id": "write-docs",
     "title": "Write docs from the code",
     "blurb": "Overview, working example, reference, gotchas. Nothing invented.",
     "modes": [
      "read",
      "act"
     ],
     "tags": [
      "documentation"
     ],
     "needs": "project",
     "body": "Document: {{Module, API, or feature|the main public surface}}\nReader: {{Audience|a developer using it for the first time}}\n\nWrite documentation grounded strictly in the code, with no invented behavior.\n1. Overview: what it is, the problem it solves, and when to use it (and when not to).\n2. Getting started: the smallest end-to-end example that really works, with its expected result.\n3. Reference: each public function, endpoint, command, or option, with its purpose, parameters (types, units, defaults, required), result, errors, and a short example.\n4. Common tasks: a few \"how do I…\" recipes.\n5. Gotchas and limits: footguns, side effects, and surprises.\nMatch the project's existing doc format and location. Where the code doesn't reveal something (a default, an error case), mark it TODO instead of guessing.",
     "needs_input": false
    },
    {
     "id": "docs-drift",
     "title": "Audit docs for drift",
     "blurb": "README, setup steps, and comments that no longer match the code.",
     "modes": [
      "audit",
      "audit_fix"
     ],
     "tags": [
      "documentation",
      "audit"
     ],
     "needs": "project",
     "body": "Scope: {{Scope|README, docs/, AGENTS.md, and code comments}}\n\nFind documentation that would mislead someone today.\n1. Setup and run steps: follow them literally (in a temp copy where possible) and note every step that fails, is missing, or names the wrong command, version, or variable.\n2. Claims: documented behavior, options, defaults, endpoints, and file paths that the code contradicts or no longer has.\n3. Agent and contributor docs: AGENTS.md, CONTRIBUTING, and similar rules that the codebase no longer follows, or commands that no longer exist.\n4. Comments and docstrings that describe what the code used to do.\n5. Gaps: things a newcomer needs that are not written anywhere (how to test, how to deploy, required services).\nFor each: the doc and line, what it says, what the code actually does (`path:line`), and the corrected text.",
     "needs_input": false
    },
    {
     "id": "commit-msg",
     "title": "Write the commit message and changelog",
     "blurb": "Explain the why; flag bundled changes.",
     "modes": [
      "read",
      "act"
     ],
     "tags": [
      "documentation",
      "release"
     ],
     "needs": "project",
     "body": "Turn the current change into a commit message and, if it's user-facing, a changelog entry. Read the diff (staged first, otherwise working changes) and enough surrounding code to explain why. If there is no change, say so and stop.\n1. A commit message in the repository's existing style (check `git log`): an imperative subject of about 50 characters, then a body that explains why (the problem, the approach, trade-offs, and anything risky). Reference an issue if I give one.\n2. If users will notice: a changelog entry written for them, under Added, Changed, Fixed, Removed, or Security.\n3. If anything is incompatible: a BREAKING CHANGE note with migration steps.\n4. If the change bundles unrelated work, say so and propose how to split it into commits.\nNo filler like \"various improvements\".",
     "needs_input": false
    }
   ]
  }
 ]
};
