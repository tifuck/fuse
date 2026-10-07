# Console presentation

This folder contains the Console renderer and its styles. It is not a standalone chat application: it expects Fuse's message/trace shapes, DOM skeleton, shared CSS tokens, and runtime callbacks.

## Activate or copy

Copy the entire folder, including its `styles/` directory, to a sibling theme directory. Internal JS imports and CSS imports are relative; they require no path edits. A developer activates the copy by changing the import in `static/chat.js` and the theme stylesheet links in the chat and settings templates. There is no user-facing mode switch or saved-mode selection.

```js
import { createConsoleTheme } from './themes/my-theme/index.js';
```

Keep `chat.js` loaded as `type="module"`. There is no bundler. Relative module URLs are intentionally unversioned: the server must revalidate them, not serve them with immutable caching. If deployment policy changes, version the entire dependency graph rather than only the entry script.

Stylesheet order is significant:

1. `app.css` — shared application layout and tokens.
2. `themes/<theme>/theme.css` — theme entry and base presentation.
3. `chat-design.css` — shared chat design adjustments.
4. `themes/<theme>/styles/design.css` — theme-specific design overrides.

The Console renderer still uses Console DOM classes and `data-view="console"` / `data-chat-console="true"`. Copying the folder changes the implementation selected by the developer, not those markup contracts. A copied theme may retain these selectors or deliberately update its markup and styles together.

## Runtime interface

`createConsoleTheme(runtime)` creates one renderer instance and returns the public callbacks used by the existing chat closure. `index.js` is the authoritative, explicit dependency wiring: each component receives only its named dependencies through getters. Component factories export `create(deps)`; descriptive import aliases identify their role in composition.

The runtime adapter at the start of `chat.js` supplies 113 named dependencies. This is a source-preserving boundary, not a minimal or stable third-party plugin API:

- **Live data:** messages, current main/subagent runs, jobs, agents, editing state, thinking preference, token meter and reduced-motion preference.
- **DOM handles:** root, transcript, log, live docks, spinner elements and latest-message indicator.
- **Domain callbacks:** execution identity/state, failure/background classification, tool presentation, usage estimates, permissions, todo normalization and agent/job timing.
- **Actions and lifecycle:** editing, retry/rewind/delete, persistence of disclosures, scrolling/follow control, painting, permission hydration and live-clock scheduling.
- **Mutable presentation coordination:** spinner frame/timer, dock animation handles, activity sequence, deferred paint flags and pending tool paint. Explicit setters preserve writes back into the runtime closure. Getter-only dependencies are read-only bindings, although objects such as messages may still be updated by rendering.

Do not destructure live state out of `deps`: a snapshot would go stale when the runtime changes threads or replaces a run. Functions can call other components through the same getter-backed wiring. Factories must not eagerly read runtime state during construction; some runtime declarations initialize later in the chat closure. Constants and caches owned entirely by presentation live in the component factories.

The 36 returned bindings cover user/assistant rendering and updating, message actions, tool/job/agent/todo/permission HTML, markdown patching, live docks/status/clocks, thinking disclosure and attachment hydration. `ACTION_ICONS` and `FILE_CHIP_ICON` are returned constants. The explicit return object in `index.js` is the complete public surface.

Browser APIs remain ambient dependencies (`document`, `window`, timers, animation frames, clipboard and selection). Markdown syntax highlighting uses the page's optional `window.hljs`; transcript patching uses the existing `FuseChatDOM` helper. This folder does not provision those shared facilities.

## Responsibilities

- `markdown-syntax.js`, `markdown-stream.js`: safe inline/block markup, highlighting, ANSI, citations and incremental markdown reveal.
- `message-actions.js`, `attachments-and-sources.js`, `user-turn.js`: message controls, files/citations and user/wake turns.
- `assistant-disclosure.js`, `transcript.js`: assistant DOM creation, disclosure behavior and incremental transcript updates.
- `tool-formatting.js`, `tool-headers.js`, `tool-results.js`, `tool-rows.js`: tool subjects and arguments, headers, folded results, expanded output and final rows.
- `shell-semantics.js`: shell parsing/classification, git/PR summaries, exit meanings and recognized edits.
- `verb-lexicon.js`, `call-semantics.js`: inflection vocabulary, human-readable counts/durations, thought timing and error formatting.
- `call-progress.js`, `activity-groups.js`: individual/parallel agent progress, wake notices, summary groups and their clocks.
- `live-dock.js`, `live-status.js`, `thinking-disclosure.js`: active-run dock/spinner, token/timing labels and thought scrolling/disclosure.
- `todos.js`, `permissions.js`, `work-panels.js`: todo and permission cards, background work rows and compaction markers.

Networking, WebSocket synchronization, durable message state, persistence, command execution, permissions decisions, sidebar/composer orchestration and transcript lifecycle stay in `chat.js`. Rendering calls those shared operations through the adapter; it does not implement another transport or store.

## Verification and extraction record

The snapshot parent contains repeatable extraction scripts, source-chunk hashes and dependency manifests. `check-theme.cjs` checks syntax, free references and removed-mode identifiers. `verify-renderers.mjs` compares representative renderer outputs to the folded source. These checks complement, not replace, browser regression tests for focus, disclosure, streaming, scrolling and permissions. Browser fixture loaders must treat `chat.js` as a module; component-level helpers may instantiate the `create(deps)` factories directly.
