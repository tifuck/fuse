// Slash palette, skills, composer suggestions, retry hint.
import { S } from '../state.js';
import { escapeHtml } from '../core.js';

const SLASH_MAX = 24;

function composerPrefs() {
  if (window.FuseChatAppearance && typeof FuseChatAppearance.getComposerPrefs === "function") {
    return FuseChatAppearance.getComposerPrefs();
  }
  return { spell: true, palettes: true, history: true };
}

function paletteKicker(kind) {
  if (kind === "at") return ["Mentions", "Tab to insert"];
  if (kind === "path") return ["Paths", "Tab to insert"];
  if (kind === "history") return ["Earlier prompts", "Enter to insert"];
  return ["Commands", "Tab to complete · Enter to select"];
}

function slashValue(cmd, extra) {
  const arg = extra == null ? "" : String(extra).trim();
  if (arg) return "/" + cmd.name + " " + arg;
  return "/" + cmd.name + (cmd.args ? " " : "");
}

export function createSlash(ctx) {
  const { context, input, net, panels, perms, picker, sidebar, sync, turns } = ctx;
    function skillsOn() {
    return Boolean(S.config.coding) || Boolean(sidebar.sessionProject());
  }

    function skillCatalog() {
    return Array.isArray(S.config.skills) ? S.config.skills : [];
  }

    function skillInvokeFromText(text) {
    if (!skillsOn()) return null;
    const match = String(text || "").trim().match(/^\/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s+([\s\S]*))?$/i);
    if (!match) return null;
    const name = match[1].toLowerCase();
    if (S.SLASH_COMMANDS.some((cmd) => cmd.name === name)) return null;
    const skill = skillCatalog().find((item) => item.slug === name && item.user_invocable !== false);
    if (!skill) return null;
    return { name, arguments: (match[2] || "").trim() };
  }

    async function refreshSkills() {
    try {
      const data = await net.api("/skills/index");
      if (data && Array.isArray(data.skills)) S.config.skills = data.skills;
    } catch (_) {}
  }

    function slashQuery() {
    const text = S.input.value;
    if (!text.startsWith("/") || text.includes("\n")) return null;
    const match = text.match(/^\/([a-z0-9]*(?:-[a-z0-9]*)*)?(?:(\s+)([\s\S]*))?$/i);
    if (!match) return null;
    return {
      name: (match[1] || "").toLowerCase(),
      spaced: Boolean(match[2]),
      arg: (match[3] || "").trim(),
      raw: text,
    };
  }

    function slashAvailable(cmd) {
    if (!cmd) return false;
    if (cmd.needs === "project") return Boolean(sidebar.sessionProject());
    if (cmd.needs === "messages") return S.messages.length > 0;
    if (cmd.needs === "redo") return S.redoStack.length > 0;
    return true;
  }

    function filteredSlash() {
    const query = slashQuery();
    if (!query || query.spaced) return [];
    const q = query.name;
    const builtins = S.SLASH_COMMANDS.filter((cmd) => !q || cmd.name.startsWith(q));
    const skills = skillsOn()
      ? skillCatalog()
          .filter((item) => item.user_invocable !== false && (!q || String(item.slug || "").startsWith(q)))
          .map((item) => ({
            name: item.slug,
            hint: item.description || "",
            args: true,
            skill: true,
          }))
      : [];
    return builtins.concat(skills).slice(0, SLASH_MAX);
  }

    function restoreEnterKeyHint() {
    if (S.input) S.input.setAttribute("enterkeyhint", "send");
  }

    function setEnterKeyHintEnter() {
    if (S.input) S.input.setAttribute("enterkeyhint", "enter");
  }

    function syncComposerAria() {
    if (!S.input) return;
    const spellOpen = window.FuseComposerSpell && FuseComposerSpell.isOpen();
    const palOpen = S.slashMenu && !S.slashMenu.hidden;
    // Focus stays in the textarea, so the highlighted option is named by its
    // aria-activedescendant (the spell popover sets its own).
    if (spellOpen) {
      S.input.setAttribute("aria-controls", "chat-spell-pop");
    } else if (palOpen) {
      S.input.setAttribute("aria-autocomplete", "list");
      S.input.setAttribute("aria-controls", "chat-slash-list");
      if (S.slashActiveId) S.input.setAttribute("aria-activedescendant", S.slashActiveId);
      else S.input.removeAttribute("aria-activedescendant");
    } else {
      S.input.removeAttribute("aria-controls");
      S.input.removeAttribute("aria-autocomplete");
      S.input.removeAttribute("aria-activedescendant");
    }
  }

    function getComposerContext() {
    const userMsgs = S.messages.filter((m) => m.role === "user" && !m.auto);
    return {
      skills: S.config.skills,
      slash: S.SLASH_COMMANDS,
      models: S.allModels,
      threads: S.threads,
      projects: S.projects,
      project: sidebar.sessionProject(),
      traces: S.messages.flatMap((m) => m.tools || []),
      attachments: S.pendingAttachments.concat(userMsgs.flatMap((m) => m.attachments || [])),
      prompts: userMsgs.map((m) => m.content),
      listing: S.projectListingCache,
    };
  }

    function closeSlash() {
    S.slashKind = "slash";
    S.paletteTrigger = null;
    S.paletteItems = [];
    if (S.slashMenu) {
      S.slashMenu.hidden = true;
      S.slashMenu.removeAttribute("data-kind");
    }
    if (S.slashList) S.slashList.innerHTML = "";
    restoreEnterKeyHint();
    syncComposerAria();
  }

    function extraPaletteOpen() {
    return S.slashMenu && !S.slashMenu.hidden && S.slashKind !== "slash";
  }

    async function refreshProjectListing() {
    S.projectListingCache = null;
    const bound = sidebar.sessionProject();
    if (!bound || S.config.vault === "locked" || !bound.host_id) return;
    try {
      const data = await net.api(`/hosts/${bound.host_id}/browse`, {
        method: "POST",
        body: JSON.stringify({ path: bound.remote_path || "" }),
      });
      S.projectListingCache = {
        path: data.path || bound.remote_path,
        entries: (data.entries || []).slice(0, 200),
      };
    } catch (_) {}
  }

    function acceptPaletteItem(item) {
    if (!item || item.disabled) return;
    const start = S.paletteTrigger ? S.paletteTrigger.start : 0;
    const end = S.paletteTrigger ? S.paletteTrigger.end : (S.slashKind === "history" ? 0 : 0);
    S.input.setRangeText(item.insert, start, end, "end");
    closeSlash();
    S.drafts.set(S.currentId, S.input.value);
    input.resizeInput();
    input.syncComposer();
    input.syncEmptyComposer();
    S.input.focus();
    if (window.FuseComposerSpell) FuseComposerSpell.refresh();
  }

    function acceptCurrentPalette() {
    if (S.slashKind === "slash") {
      const items = filteredSlash();
      if (items[S.slashIndex]) acceptSlash(items[S.slashIndex]);
      return;
    }
    const item = S.paletteItems[S.slashIndex];
    if (item) acceptPaletteItem(item);
  }

    function renderPalette(kind, rows, trigger) {
    if (!S.slashMenu || !S.slashList) return;
    S.slashKind = kind;
    S.paletteTrigger = trigger || null;
    S.paletteItems = rows || [];
    if (!S.paletteItems.length && kind !== "at") {
      closeSlash();
      return;
    }
    if (S.slashIndex >= S.paletteItems.length) S.slashIndex = Math.max(0, S.paletteItems.length - 1);
    if (S.slashIndex < 0) S.slashIndex = 0;
    turns.closeRewind();
    context.closeContextPop();
    if (window.FuseComposerSpell && FuseComposerSpell.isOpen()) FuseComposerSpell.close();
    const [title, hint] = paletteKicker(kind);
    if (S.slashKicker) S.slashKicker.innerHTML = `${escapeHtml(title)} <span>${escapeHtml(hint)}</span>`;
    S.slashMenu.hidden = false;
    S.slashMenu.setAttribute("data-kind", kind);
    S.slashList.innerHTML = "";
    S.slashActiveId = "";
    if (!S.paletteItems.length) {
      const empty = document.createElement("p");
      empty.className = "chat-slash-empty";
      empty.textContent = "No matches";
      S.slashList.appendChild(empty);
    } else {
      S.paletteItems.forEach((item, index) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "chat-slash-item" + (index === S.slashIndex ? " is-active" : "");
        btn.setAttribute("role", "option");
        btn.setAttribute("aria-selected", String(index === S.slashIndex));
        btn.tabIndex = -1;
        btn.id = "chat-slash-" + kind + "-" + index;
        const label = item.kind === "skill" ? item.text : item.text;
        btn.innerHTML = `<kbd>${escapeHtml(label)}</kbd><span>${escapeHtml(item.hint || item.kind || "")}</span>`;
        btn.addEventListener("mousedown", (event) => event.preventDefault());
        btn.addEventListener("click", () => acceptPaletteItem(item));
        S.slashList.appendChild(btn);
      });
      S.slashActiveId = "chat-slash-" + kind + "-" + S.slashIndex;
      const active = S.slashList.querySelector(".is-active");
      if (active) active.scrollIntoView({ block: "nearest" });
    }
    setEnterKeyHintEnter();
    syncComposerAria();
  }

    function openHistoryPeek() {
    if (!composerPrefs().history) return;
    const prompts = window.FuseComposerComplete
      ? FuseComposerComplete.history(getComposerContext())
      : [];
    if (!prompts.length) return;
    S.slashIndex = 0;
    renderPalette("history", prompts.map((text) => ({
      kind: "history",
      text: text.length > 72 ? text.slice(0, 69) + "…" : text,
      insert: text,
      hint: "prompt",
    })), { start: 0, end: 0, kind: "history", query: "" });
  }

    function renderComposerSuggestions() {
    if (window.FuseComposerSpell && FuseComposerSpell.composing) {
      closeSlash();
      return;
    }
    const q = slashQuery();
    if (q) {
      S.slashKind = "slash";
      if (filteredSlash().length) renderSlash();
      else closeSlash();
      return;
    }
    const palettesOn = composerPrefs().palettes;
    if (palettesOn && window.FuseComposerComplete) {
      const t = FuseComposerComplete.trigger(S.input.value, S.input.selectionStart, getComposerContext());
      if (t && t.kind === "at") {
        S.slashIndex = 0;
        renderPalette("at", FuseComposerComplete.items(t, getComposerContext()), t);
        return;
      }
      if (t && t.kind === "path") {
        const rows = FuseComposerComplete.items(t, getComposerContext());
        if (rows.length) {
          S.slashIndex = 0;
          renderPalette("path", rows, t);
          return;
        }
      }
    }
    closeSlash();
  }

    function acceptSlash(cmd) {
    if (!cmd) return;
    const query = slashQuery();
    S.input.value = slashValue(cmd, query && cmd.args ? query.arg : "");
    closeSlash();
    S.drafts.set(S.currentId, S.input.value);
    input.resizeInput();
    input.syncComposer();
    input.syncEmptyComposer();
    S.input.focus();
    const end = S.input.value.length;
    try { S.input.setSelectionRange(end, end); } catch (_) {}
  }

    function renderSlash() {
    if (!S.slashMenu || !S.slashList) return;
    const items = filteredSlash();
    if (!items.length) {
      closeSlash();
      return;
    }
    S.slashKind = "slash";
    S.paletteTrigger = null;
    S.paletteItems = [];
    if (S.slashIndex >= items.length) S.slashIndex = items.length - 1;
    if (S.slashIndex < 0) S.slashIndex = 0;
    turns.closeRewind();
    context.closeContextPop();
    if (S.slashKicker) S.slashKicker.innerHTML = `Commands <span>Tab to complete · Enter to select</span>`;
    S.slashMenu.hidden = false;
    S.slashMenu.setAttribute("data-kind", "slash");
    S.slashList.innerHTML = "";
    items.forEach((cmd, index) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-slash-item" + (index === S.slashIndex ? " is-active" : "");
      if (!slashAvailable(cmd)) btn.classList.add("is-off");
      btn.setAttribute("role", "option");
      btn.setAttribute("aria-selected", String(index === S.slashIndex));
      btn.tabIndex = -1;
      btn.id = "chat-slash-" + cmd.name;
      btn.innerHTML = `<kbd>/${escapeHtml(cmd.name)}</kbd><span>${escapeHtml(cmd.hint)}</span>`;
      btn.addEventListener("mousedown", (event) => event.preventDefault());
      btn.addEventListener("click", () => acceptSlash(cmd));
      S.slashList.appendChild(btn);
    });
    S.slashActiveId = items[S.slashIndex] ? "chat-slash-" + items[S.slashIndex].name : "";
    const active = S.slashList.querySelector(".is-active");
    if (active) active.scrollIntoView({ block: "nearest" });
    setEnterKeyHintEnter();
    syncComposerAria();
  }

    function runSlash(name, arg) {
    closeSlash();
    const query = slashQuery();
    const rest = arg != null ? arg : (query && query.arg) || "";
    if (name === "undo") {
      const times = Math.max(1, Math.min(20, parseInt(rest, 10) || 1));
      return sidebar.undoConversation(times);
    }
    S.input.value = "";
    input.resizeInput();
    input.syncComposer();
    if (name === "retry") {
      turns.retryLastTurn();
      return;
    }
    if (name === "redo") {
      sidebar.redoConversation();
      return;
    }
    if (name === "rewind") {
      turns.openRewind();
      return;
    }
    if (name === "help") {
      picker.setHint("/undo, /retry, /redo, /rewind, /title, /recap, /permissions, /compact, /thinking, /init. Skills: type / then a name. Manage on Skills.");
      return;
    }
    if (name === "permissions") {
      perms.setPermsOpen(true);
      return;
    }
    if (name === "title") {
      sync.runTitleRegen();
      return;
    }
    if (name === "recap") { sync.runRecap(); return; }
    if (name === "thinking") {
      const want = rest.trim().toLowerCase();
      panels.setShowThinking(/^(on|show|shown|yes|true)$/.test(want) ? true : /^(off|hide|hidden|no|false)$/.test(want) ? false : !S.showThinking);
      picker.setHint(S.showThinking ? "Thinking is shown." : "Thinking is hidden. The status line still says when the model is thinking.");
      return;
    }
    if (name === "compact") {
      context.runCompact(rest);
      return;
    }
    if (name === "init") {
      if (!sidebar.sessionProject()) {
        picker.setHint("/init only works in a project chat.");
        return;
      }
      turns.completeTurn(rest ? "/init " + rest : "/init");
    }
  }

  return {
    acceptCurrentPalette,
    acceptSlash,
    closeSlash,
    extraPaletteOpen,
    filteredSlash,
    getComposerContext,
    openHistoryPeek,
    refreshProjectListing,
    refreshSkills,
    renderComposerSuggestions,
    renderPalette,
    renderSlash,
    restoreEnterKeyHint,
    runSlash,
    skillInvokeFromText,
    slashQuery,
    syncComposerAria,
    composerPrefs,
  };
}

export { composerPrefs };
