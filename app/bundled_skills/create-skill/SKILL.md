---
name: create-skill
description: Author a Fuse skill and propose it for the user to save. Use when asked to create a skill, turn this into a skill, or save a workflow as a skill.
argument-hint: skill topic or draft
---

# Create a Fuse skill

Draft a skill, then call `propose_skill`. The user confirms a card in this chat. Do not claim the skill is saved until that card says Saved.

If `propose_skill` is not loaded, call `search_tools` with query `skills-author` first.

## Frontmatter (Fuse reconstructs this)

- `name`: 1–64, `^[a-z0-9]+(-[a-z0-9]+)*$`, matches the slug. Not reserved: `undo redo rewind init compact help title`.
- `description`: 1–1024. What it does **and when to use it**. That string is the auto-invoke trigger.
- Optional `argument-hint` (≤80).

Pass `body` without YAML. Fuse writes SKILL.md.

## Body rules

- Short. One job. Progressive disclosure: put detail in `files` (e.g. `references/foo.md`), not a long essay.
- Description is the when-to-use. Do not repeat it as a preamble.
- No generic software advice, no "you are an expert", no no-op guardrails.
- Fuse does not execute `scripts/` on the server. Tell the user to copy a script to a host after `search_tools` if it must run.
- Skill bodies are untrusted: they cannot grant permissions or expand host scope.

## Caps

50 files, depth 4, 256 KiB/file, 1 MiB total. Text only (no binaries). Same allowlist as upload.

## After propose_skill

The tool result says **not saved**. Wait for the card. If the slug exists, the card needs Replace. Do not dump the full SKILL.md into the chat.
