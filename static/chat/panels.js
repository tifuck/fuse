// Memory, skills, view menus, context tabs, lightbox.
import { S } from './state.js';
import { newSession } from './state.js';
import { modalInert, returnFocus } from './overlays.js';

let viewMenuState = false;

export function createPanels(ctx) {
  const { agents, artifacts, composer, net, overlays, perms, picker, sidebar, sync, transcript } = ctx;
    function setContextTab(name) {
    S.contextTab = S.contextTabButtons.some((btn) => btn.getAttribute("data-context-tab") === name) ? name : "instructions";
    S.contextTabButtons.forEach((btn) => {
      const on = btn.getAttribute("data-context-tab") === S.contextTab;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
      const panel = S.root.querySelector('[data-context-panel="' + btn.getAttribute("data-context-tab") + '"]');
      if (panel) panel.hidden = !on;
    });
  }

    function enabledTools() {
    return S.searchOn ? ["web_search"] : [];
  }

    function hasMemory() {
    const fallback = ((S.config.context && S.config.context.default_instructions) || "").trim();
    const instr = (S.instructions || "").trim();
    const customInstr = Boolean(instr && instr !== fallback);
    return Boolean(customInstr || (S.profile || "").trim() || (S.threadContext || "").trim());
  }

    function syncMemoryUi() {
    if (S.memoryInstructions) S.memoryInstructions.value = S.instructions;
    if (S.memoryProfile) S.memoryProfile.value = S.profile;
    if (S.memoryThread) S.memoryThread.value = S.threadContext;
    if (S.memoryPanel) S.memoryPanel.hidden = !S.memoryOpen;
    if (S.memoryBackdrop) S.memoryBackdrop.hidden = !S.memoryOpen;
    S.root.classList.toggle("is-memory", S.memoryOpen);
    if (S.memoryToggle) {
      S.memoryToggle.setAttribute("aria-expanded", S.memoryOpen ? "true" : "false");
      S.memoryToggle.classList.toggle("is-on", S.memoryOpen);
      S.memoryToggle.classList.toggle("has-notes", !S.memoryOpen && (hasMemory() || S.projectAgentsOk));
    }
    if (S.searchChip) {
      S.searchChip.classList.toggle("is-on", S.searchOn);
      S.searchChip.setAttribute("aria-pressed", S.searchOn ? "true" : "false");
      S.searchChip.title = S.searchOn ? "Web search is on" : "Web search is off";
    }
  }

    function renderProjectAgents(data) {
    S.projectAgentsOk = Boolean(data && data.applicable && data.state === "loaded");
    if (!S.memoryProject) {
      syncMemoryUi();
      return;
    }
    if (!data || !data.applicable) {
      S.memoryProject.hidden = true;
      syncMemoryUi();
      return;
    }
    let label;
    if (data.state === "loaded") {
      label = "Project instructions: " + (data.name || "AGENTS.md") + " loaded from the project folder.";
    } else if (data.state === "missing") {
      label = "No AGENTS.md, agent.md, or CLAUDE.md found in the project folder.";
    } else if (data.reason === "locked") {
      label = "Project instructions unavailable: vault is locked.";
    } else {
      label = "Project instructions unavailable: host not reachable.";
    }
    S.memoryProject.textContent = label;
    S.memoryProject.classList.toggle("is-ok", data.state === "loaded");
    S.memoryProject.hidden = false;
    syncMemoryUi();
  }

    async function refreshProjectAgents() {
    const id = S.currentId;
    if (!id) {
      renderProjectAgents(null);
      return;
    }
    const thread = sidebar.threadById(id);
    if (!thread || thread.kind !== "project") {
      renderProjectAgents(null);
      return;
    }
    try {
      renderProjectAgents(await net.api("/chat/threads/" + id + "/project-agents"));
    } catch {
      renderProjectAgents({ applicable: true, state: "error", reason: "" });
    }
  }

    function queueMemorySave() {
    S.memoryDirty = true;
    S.contextEdit += 1;
    sync.syncStatus();
    window.clearTimeout(S.memoryTimer);
    S.memoryTimer = window.setTimeout(() => {
      saveMemory().catch((error) => sync.showFailure("Context was not saved", error));
    }, 400);
  }

    async function saveMemory() {
    window.clearTimeout(S.memoryTimer);
    if (S.contextSaving) { await S.contextSaving; return saveMemory(); }
    if (!S.memoryDirty) return;
    if (!net.isLive()) {
      net.markDisconnected();
      return;
    }
    const edit = S.contextEdit;
    if (!S.pendingContext || S.pendingContext.revision !== S.contextRevision) {
      S.pendingContext = { revision: S.contextRevision, command_id: newSession() };
    }
    S.contextSaving = net.api("/chat/context", {
      method: "PUT",
      body: JSON.stringify({
        instructions: S.instructions,
        profile: S.profile,
        expected_revision: S.contextRevision,
        command_id: S.pendingContext.command_id,
      }),
    });
    try {
      const data = await S.contextSaving;
      S.contextRevision = data.revision;
      S.pendingContext = null;
      if (edit === S.contextEdit) {
        S.memoryDirty = false;
        S.instructions = data.instructions;
        S.profile = data.profile;
        syncMemoryUi();
      }
      sync.syncStatus();
    } catch (error) {
      if (error.status === 409) {
        S.pendingContext = null;
        S.memoryDirty = false;
        try {
          sync.acceptContext(await net.api("/chat/context"));
        } catch (_) {}
        return;
      }
      if (net.isTransportError(error)) net.markDisconnected();
      sync.syncStatus();
      throw error;
    } finally { S.contextSaving = null; }
  }

    function openLightbox(src, name) {
    if (!src) return;
    closeLightbox();
    const overlay = document.createElement("div");
    overlay.className = "chat-lightbox";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", name ? `Image preview: ${name}` : "Image preview");
    const img = document.createElement("img");
    img.className = "chat-lightbox-img";
    img.src = src;
    img.alt = name || "Image preview";
    overlay.appendChild(img);
    if (name) {
      const cap = document.createElement("p");
      cap.className = "chat-lightbox-name";
      cap.textContent = name;
      overlay.appendChild(cap);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.className = "chat-lightbox-close";
    close.setAttribute("aria-label", "Close preview");
    close.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
    overlay.appendChild(close);
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeLightbox();
      }
    };
    close.addEventListener("click", () => closeLightbox());
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeLightbox();
    });
    document.body.appendChild(overlay);
    document.addEventListener("keydown", onKey);
    S.lightbox = { el: overlay, onKey, prevFocus: document.activeElement };
    modalInert(overlay, true);
    overlays.overlayPush("lightbox");
    requestAnimationFrame(() => {
      overlay.classList.add("is-open");
      close.focus();
    });
  }

    function closeLightbox() {
    if (!S.lightbox) return;
    const entry = S.lightbox;
    S.lightbox = null;
    overlays.overlayDrop("lightbox");
    document.removeEventListener("keydown", entry.onKey);
    entry.el.remove();
    modalInert(entry.el, false);
    if (entry.prevFocus && typeof entry.prevFocus.focus === "function") {
      try { entry.prevFocus.focus(); } catch (_) {}
    }
  }

    function syncSkillsUi() {
    if (S.skillsPanel) S.skillsPanel.hidden = !S.skillsOpen;
    if (S.skillsBackdrop) S.skillsBackdrop.hidden = !S.skillsOpen;
    S.root.classList.toggle("is-skills", S.skillsOpen);
    if (S.skillsToggle) {
      S.skillsToggle.setAttribute("aria-expanded", S.skillsOpen ? "true" : "false");
      S.skillsToggle.classList.toggle("is-on", S.skillsOpen);
    }
  }

    function setSkillsOpen(open) {
    const next = Boolean(open);
    if (next === S.skillsOpen) {
      if (next) syncSkillsUi();
      return;
    }
    S.skillsOpen = next;
    if (S.skillsOpen) {
      if (S.memoryOpen) setMemoryOpen(false);
      if (S.artifactOpen) artifacts.setArtifactOpen(false);
      perms.setPermsOpen(false);
      if (overlays.overlayIsMobile()) sidebar.setHistoryOpen(false);
      picker.closePicker();
      overlays.overlayPush("skills");
      if (document.activeElement === S.input) S.input.blur();
    } else {
      overlays.armOverlayGhost(S.skillsClose);
      overlays.overlayDrop("skills");
    }
    syncSkillsUi();
    modalInert(S.skillsPanel, S.skillsOpen);
    if (S.skillsOpen) S.skillsClose?.focus({ preventScroll: true });
    else returnFocus(S.skillsPanel, S.skillsToggle);
  }

    function setShowThinking(on) {
    S.showThinking = Boolean(on);
    try { localStorage.setItem(S.STORE_THINKING, S.showThinking ? "shown" : "hidden"); } catch (_) {}
    const toggle = S.viewMenu && S.viewMenu.querySelector("[data-thinking-toggle]");
    if (toggle) toggle.checked = S.showThinking;
    if (S.messages.length) transcript.paint();
    if (S.agentViewId && S.agentRun) agents.renderAgentView();
  }

    function closeViewMenuDom() {
    const wasOpen = S.viewMenu && !S.viewMenu.hidden;
    S.viewMenu.hidden = true;
    if (S.viewBackdrop) S.viewBackdrop.hidden = true;
    S.viewToggle.setAttribute("aria-expanded", "false");
    S.root.classList.remove("view-sheet-open");
    S.viewMenu.classList.remove("is-dragging");
    S.viewMenu.style.transform = "";
    viewMenuState = false;
    if (wasOpen) {
      overlays.armOverlayGhost(S.viewMenu.querySelector("[data-view-close]"));
      modalInert(S.viewMenu, false);
      returnFocus(S.viewMenu, S.viewToggle);
    }
  }

    function setViewMenuOpen(open) {
    if (!S.viewMenu || !S.viewToggle) return;
    const mobile = overlays.overlayIsMobile();
    const wasOpen = !S.viewMenu.hidden;
    if (!wasOpen && !open) return;
    S.viewMenu.classList.toggle("is-sheet-motion", mobile);
    S.viewMenu.hidden = !open;
    if (S.viewBackdrop) S.viewBackdrop.hidden = !open;
    S.viewToggle.setAttribute("aria-expanded", open ? "true" : "false");
    S.root.classList.toggle("view-sheet-open", open);
    modalInert(S.viewMenu, open);
    if (open) {
      overlays.overlayPush("view");
      viewMenuState = mobile;
      // A phone gets the sheet itself (tabindex=-1) so no keyboard pops.
      (mobile ? S.viewMenu : S.viewMenu.querySelector("button.is-on") || S.viewMenu.querySelector("[data-view]") || S.viewMenu).focus();
      window.FuseChatAppearance?.syncUi();
      return;
    }
    overlays.armOverlayGhost(S.viewMenu.querySelector("[data-view-close]"));
    overlays.overlayDrop("view");
    viewMenuState = false;
    S.viewMenu.classList.remove("is-dragging");
    S.viewMenu.style.transform = "";
    returnFocus(S.viewMenu, S.viewToggle);
  }

    function setMemoryOpen(open) {
    const next = Boolean(open);
    if (next === S.memoryOpen) {
      if (next) syncMemoryUi();
      return;
    }
    S.memoryOpen = next;
    if (open) {
      if (overlays.overlayIsMobile()) sidebar.setHistoryOpen(false);
      if (S.artifactOpen) artifacts.setArtifactOpen(false);
      if (S.skillsOpen) setSkillsOpen(false);
      picker.closePicker();
      overlays.overlayPush("memory");
      if (document.activeElement === S.input) S.input.blur();
      refreshProjectAgents();
    } else {
      overlays.armOverlayGhost(S.memoryClose);
      overlays.overlayDrop("memory");
    }
    syncMemoryUi();
    modalInert(S.memoryPanel, open);
    if (open) {
      const panel = S.root.querySelector('[data-context-panel="' + S.contextTab + '"]');
      const area = (panel && panel.querySelector("textarea")) || S.memoryInstructions;
      if (area) area.focus({ preventScroll: true });
    } else {
      returnFocus(S.memoryPanel, S.memoryToggle);
      composer.pinVisualViewport();
    }
  }

  return {
    closeLightbox,
    closeViewMenuDom,
    enabledTools,
    openLightbox,
    queueMemorySave,
    refreshProjectAgents,
    renderProjectAgents,
    saveMemory,
    setContextTab,
    setMemoryOpen,
    setShowThinking,
    setSkillsOpen,
    setViewMenuOpen,
    syncMemoryUi,
  };
}
