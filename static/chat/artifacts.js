// Artifact pane: open, versions, source view.
import { S } from './state.js';
import { escapeHtml } from './core.js';
import { returnFocus } from './overlays.js';

function artifactContentUrl(id, version) {
  const path = "/a/" + encodeURIComponent(id) + "/content";
  return version ? path + "?v=" + encodeURIComponent(version) : path;
}

export function createArtifacts(ctx) {
  const { agents, composer, net, overlays, panels, picker, sidebar } = ctx;
    function artifactsInThread() {
    const out = [];
    S.messages.forEach((msg) => {
      (msg.tools || []).forEach((trace) => {
        if (
          (trace.name === "create_artifact" || trace.name === "update_artifact") &&
          trace.identifier &&
          trace.status !== "error"
        ) {
          out.push(trace);
        }
      });
    });
    return out;
  }

    function latestArtifact() {
    const all = artifactsInThread();
    return all.length ? all[all.length - 1] : null;
  }

    function rememberLatestArtifact() {
    const last = latestArtifact();
    if (!last) {
      S.currentArtifact = null;
      // Close through setArtifactOpen so the mobile overlay/history entry is
      // dropped too; a bare flag write leaves a Back press that does nothing.
      if (S.artifactOpen) setArtifactOpen(false);
      else syncArtifactUi();
      return;
    }
    if (S.currentArtifact && S.currentArtifact.building) {
      syncArtifactUi();
      return;
    }
    S.currentArtifact = {
      identifier: last.identifier,
      title: last.title || "Artifact",
      visibility: last.visibility || "",
      version: last.version,
      url: last.url || "/a/" + last.identifier,
    };
    syncArtifactUi();
  }

    function syncArtifactUi() {
    if (S.artifactPanel) S.artifactPanel.hidden = !S.artifactOpen;
    if (S.artifactBackdrop) S.artifactBackdrop.hidden = !(S.artifactOpen && window.matchMedia("(max-width: 800px)").matches);
    S.root.classList.toggle("is-artifact", S.artifactOpen);
    if (S.artifactToggle) {
      const has = Boolean(latestArtifact() || (S.currentArtifact && S.currentArtifact.building));
      S.artifactToggle.disabled = !has;
      S.artifactToggle.setAttribute("aria-disabled", has ? "false" : "true");
      S.artifactToggle.setAttribute("aria-pressed", S.artifactOpen ? "true" : "false");
      S.artifactToggle.setAttribute("aria-expanded", S.artifactOpen ? "true" : "false");
      S.artifactToggle.classList.toggle("is-on", S.artifactOpen);
      S.artifactToggle.title = has ? "Artifact preview" : "No artifact in this chat yet";
    }
    const building = Boolean(S.currentArtifact && S.currentArtifact.building);
    const ident = S.currentArtifact && S.currentArtifact.identifier;
    if (S.artifactTitleEl) S.artifactTitleEl.textContent = (S.currentArtifact && S.currentArtifact.title) || "Artifact";
    if (S.artifactStatusEl) {
      S.artifactStatusEl.hidden = !building;
      S.artifactStatusEl.textContent = building ? "Building…" : "";
    }
    if (S.artifactVisibilityEl) {
      const vis = S.currentArtifact && S.currentArtifact.visibility;
      S.artifactVisibilityEl.hidden = !vis;
      S.artifactVisibilityEl.textContent = vis || "";
      S.artifactVisibilityEl.classList.toggle("on", vis === "public");
    }
    if (S.artifactOpenLink) {
      if (ident) {
        S.artifactOpenLink.hidden = false;
        S.artifactOpenLink.href = "/a/" + encodeURIComponent(ident);
      } else {
        S.artifactOpenLink.hidden = true;
        S.artifactOpenLink.removeAttribute("href");
      }
    }
    if (S.artifactBuilding) S.artifactBuilding.hidden = !building;
    if (S.artifactFrame) {
      S.artifactFrame.hidden = building || S.artifactMode !== "preview" || !ident;
    }
    if (S.artifactSource) S.artifactSource.hidden = building || S.artifactMode !== "source";
    S.artifactModeBtns.forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.getAttribute("data-artifact-mode") === S.artifactMode ? "true" : "false");
    });
    const versions = (S.currentArtifact && S.currentArtifact.versions) || [];
    if (S.artifactVersionWrap && S.artifactVersionEl) {
      if (versions.length > 1) {
        S.artifactVersionWrap.hidden = false;
        const current = String(S.currentArtifact.version || versions[versions.length - 1]);
        if (S.artifactVersionEl._sig !== versions.join(",") + current) {
          S.artifactVersionEl._sig = versions.join(",") + current;
          S.artifactVersionEl.innerHTML = versions
            .slice()
            .reverse()
            .map((ver) => `<option value="${escapeHtml(String(ver))}"${String(ver) === current ? " selected" : ""}>v${escapeHtml(String(ver))}</option>`)
            .join("");
        }
      } else {
        S.artifactVersionWrap.hidden = true;
      }
    }
  }

    function setArtifactOpen(open) {
    const next = Boolean(open);
    if (next === S.artifactOpen) {
      if (next) syncArtifactUi();
      return;
    }
    S.artifactOpen = next;
    if (S.artifactOpen) {
      if (S.memoryOpen) panels.setMemoryOpen(false);
      if (S.skillsOpen) panels.setSkillsOpen(false);
      if (overlays.overlayIsMobile()) sidebar.setHistoryOpen(false);
      else if (window.matchMedia("(max-width: 1100px)").matches) {
        S.root.classList.add("history-collapsed");
        if (typeof sidebar.syncHistory === "function") sidebar.syncHistory();
      }
      picker.closePicker();
      overlays.overlayPush("artifact");
    } else {
      overlays.armOverlayGhost(S.root.querySelector("[data-artifact-close]"));
      overlays.overlayDrop("artifact");
    }
    syncArtifactUi();
    // Only a click moves focus in; a stream opening the pane must not take it.
    if (S.artifactOpen) { if (document.activeElement?.matches("button")) S.artifactPanel?.focus({ preventScroll: true }); }
    else returnFocus(S.artifactPanel, S.artifactToggle.disabled ? S.input : S.artifactToggle);
    composer.scheduleFitChatBar();
  }

    function startArtifactBuild(title) {
    S.currentArtifact = { title: title || "Artifact", building: true, identifier: "", visibility: "private" };
    S.artifactMode = "preview";
    S.artifactSourceLoaded = "";
    if (S.artifactFrame) S.artifactFrame.removeAttribute("src");
    if (S.artifactSource) S.artifactSource.textContent = "";
    setArtifactOpen(true);
    syncArtifactUi();
  }

    async function showArtifact(identifier, seed) {
    if (!identifier) return;
    const gen = S.generation;
    const prior = S.currentArtifact && S.currentArtifact.identifier === identifier ? S.currentArtifact : {};
    S.currentArtifact = {
      identifier,
      title: (seed && seed.title) || prior.title || "Artifact",
      visibility: (seed && seed.visibility) || prior.visibility || "",
      version: seed && seed.version != null ? seed.version : prior.version,
      versions: prior.versions || [],
      url: "/a/" + identifier,
      building: false,
    };
    S.artifactMode = "preview";
    S.artifactSourceLoaded = "";
    if (S.artifactSource) S.artifactSource.textContent = "";
    setArtifactOpen(true);
    const version = S.currentArtifact.version;
    if (S.artifactFrame) S.artifactFrame.src = artifactContentUrl(identifier, version);
    syncArtifactUi();
    try {
      const meta = await net.api("/a/" + encodeURIComponent(identifier) + "/meta");
      if (gen !== S.generation || !S.currentArtifact || S.currentArtifact.identifier !== identifier) return;
      S.currentArtifact.title = meta.title || S.currentArtifact.title;
      S.currentArtifact.visibility = meta.visibility || S.currentArtifact.visibility;
      S.currentArtifact.version = meta.version;
      S.currentArtifact.versions = Array.isArray(meta.versions) ? meta.versions : [];
      S.currentArtifact.url = meta.url || S.currentArtifact.url;
      if (S.artifactFrame && S.artifactFrame.getAttribute("src") !== artifactContentUrl(identifier, S.currentArtifact.version)) {
        S.artifactFrame.src = artifactContentUrl(identifier, S.currentArtifact.version);
      }
      syncArtifactUi();
    } catch (_) {}
  }

    async function loadArtifactSource() {
    if (!S.currentArtifact || !S.currentArtifact.identifier) return;
    const gen = S.generation;
    const key = S.currentArtifact.identifier + ":" + (S.currentArtifact.version || "");
    if (S.artifactSourceLoaded === key) return;
    const res = await fetch(artifactContentUrl(S.currentArtifact.identifier, S.currentArtifact.version), { credentials: "same-origin" });
    const text = await res.text();
    if (gen !== S.generation || !S.currentArtifact || key !== S.currentArtifact.identifier + ":" + (S.currentArtifact.version || "")) return;
    if (!res.ok) throw new Error("Could not load source");
    S.artifactSourceLoaded = key;
    if (S.artifactSource) S.artifactSource.textContent = text;
  }

  return {
    artifactsInThread,
    latestArtifact,
    loadArtifactSource,
    rememberLatestArtifact,
    setArtifactOpen,
    showArtifact,
    startArtifactBuild,
    syncArtifactUi,
    artifactContentUrl,
  };
}

export { artifactContentUrl };
