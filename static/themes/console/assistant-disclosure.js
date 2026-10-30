// assistant-disclosure presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function consoleGroupOpenKeys(group, msg) {
    const keys = new Set();
    group.querySelectorAll(".chat-timeline > [data-step], .chat-timeline > [data-thought]").forEach((row) => {
      keys.add("cgroup-" + (row.dataset.step || `think-${row.dataset.thought}`));
    });
    const foldable = (row) => {
      if (!row || !row.matches("li.chat-step.is-console-call[data-step]")) return false;
      const id = row.dataset.step.replace(/^tool-/, "");
      const trace = (msg?.tools || []).find((item) => item.id === id);
      return Boolean(trace && deps.ccToolClass(trace));
    };
    for (const dir of ["previousElementSibling", "nextElementSibling"]) {
      for (let row = group[dir]; foldable(row); row = row[dir]) keys.add("cgroup-" + row.dataset.step);
    }
    return keys;
  }

function renderAssistant(msg, live) {
    deps.restoreDisclosures(msg);
    const article = document.createElement("article");
    article.className = "chat-turn is-assistant";
    article._message = msg;
    article._rebind = next => { msg = next; article._message = next; };
    article.setAttribute("aria-label", "Assistant response");
    article.innerHTML =
      '<div class="chat-assistant">' +
      '<div class="chat-flow" data-flow></div>' +
      '<div class="chat-artifact-cards" data-artifact-cards hidden></div>' +
      '<details class="chat-citations" data-citations hidden><summary><span class="chat-source-badge" data-source-count></span></summary><div data-source-grid></div></details>' +
      '<ul class="chat-notice" data-notice role="status" aria-label="Fuse status" hidden></ul>' +
      '<p class="chat-error" data-err hidden></p>' +
      '<div class="chat-error-actions" data-err-actions hidden><button type="button" class="chat-error-continue" data-continue>Continue</button></div>' +
      '<div class="chat-foot" data-foot hidden><span class="chat-foot-meta" data-meta></span><div class="chat-msg-actions" data-actions-host></div></div>' +
      "</div>";
    article.addEventListener("click", (event) => {
      const summaryBtn = event.target.closest("[data-activity-summary]");
      if (!summaryBtn || summaryBtn.tagName !== "BUTTON") return;
      const activityEl = summaryBtn.closest("[data-activity]");
      if (activityEl.classList.contains("is-waiting") || activityEl.classList.contains("is-bare")) return;
      const open = !activityEl.classList.contains("is-open");
      if (open) deps.pauseThreadFollow();
      activityEl.classList.toggle("is-open", open);
      summaryBtn.setAttribute("aria-expanded", String(open));
      if (!msg.activityOpenMap) msg.activityOpenMap = {};
      msg.activityOpenMap[activityEl.dataset.activityBlock] = open;
      deps.saveDisclosures(msg);
      if (open) requestAnimationFrame(() => {
        activityEl.querySelectorAll(".is-live-think .chat-think-preview").forEach(body => deps.followThinking(body, true));
      });
    });
    function collapseConsoleGroup(group, target) {
      group.classList.remove("is-console-expanded");
      group.querySelectorAll(":scope > .chat-step-main > .chat-step-head [data-step-toggle]").forEach((el) => el.setAttribute("aria-expanded", "false"));
      if (!target.stepOpen) target.stepOpen = {};
      target.stepOpen[group.dataset.step] = false;
      consoleGroupOpenKeys(group, target).forEach((key) => { target.stepOpen[key] = false; });
      deps.saveDisclosures(target);
    }
    article.addEventListener("click", (event) => {
      const thinkingSize = event.target.closest("[data-think-size]");
      if (thinkingSize) {
        const li = thinkingSize.closest("[data-step]");
        msg.stepOpen ||= {};
        if ((li.dataset.thinkMode === "expanded")) msg.stepOpen[li.dataset.step] = false;
        else if (li.dataset.thinkMode === "expanded") delete msg.stepOpen[li.dataset.step];
        else msg.stepOpen[li.dataset.step] = true;
        deps.syncThinkingMode(li, msg, true);
        const peek = li.querySelector("[data-reason-preview]");
        peek._thinking.follow = true;
        deps.followThinking(peek);
        deps.saveDisclosures(msg);
        return;
      }
      const expand = event.target.closest("[data-group-expand]");
      if (expand) {
        const group = expand.closest(".is-stack");
        const toggles = [...group.querySelectorAll(".chat-tool-children > .chat-step > .chat-step-main > .chat-step-head > .chat-step-toggle")];
        const open = !toggles.every(toggle => toggle.getAttribute("aria-expanded") === "true");
        toggles.forEach(toggle => { if ((toggle.getAttribute("aria-expanded") === "true") !== open) toggle.click(); });
        expand.textContent = open ? "Collapse results" : "Expand results";
        return;
      }
      const prev = event.target.closest("[data-prev-toggle]");
      if (prev) {
        event.preventDefault();
        const box = prev.closest("[data-prev]");
        const reveal = box && box.querySelector(":scope > .chat-step-reveal");
        if (!reveal) return;
        const open = !reveal.classList.contains("is-open");
        reveal.classList.toggle("is-open", open);
        prev.setAttribute("aria-expanded", String(open));
        if (!msg.stepOpen) msg.stepOpen = {};
        msg.stepOpen[box.dataset.prev] = open;
        deps.saveDisclosures(msg);
        return;
      }
      const sub = event.target.closest("[data-substep-toggle]");
      if (sub) {
        event.preventDefault();
        const box = sub.closest("[data-substep]");
        const reveal = box && box.querySelector(".chat-step-reveal");
        if (!reveal) return;
        const open = !reveal.classList.contains("is-open");
        reveal.classList.toggle("is-open", open);
        sub.setAttribute("aria-expanded", String(open));
        if (!msg.stepOpen) msg.stepOpen = {};
        msg.stepOpen[box.dataset.substep] = open;
        deps.saveDisclosures(msg);
        return;
      }
      // An opened summary line has no header in Console (as in Claude Code):
      // a click on its printed calls folds it back.
      const openGroup = (event.target.closest(".chat-step.is-console-group.is-console-expanded"));
      if (openGroup && !event.target.closest("a, button, input, textarea, [data-copy-tool]") && !String(window.getSelection() || "").trim()) {
        event.preventDefault();
        collapseConsoleGroup(openGroup, msg);
        return;
      }
      const btn = event.target.closest("[data-step-toggle]");
      if (!btn || event.target.closest(".chat-step-open")) return;
      if ((event.target.closest("a"))) return;
      const li = btn.closest("[data-step]");
      if (!li) return;
      const reveal = li.querySelector(":scope > .chat-step-main > .chat-step-reveal");
      const hang = li.querySelector(":scope > .chat-step-main .chat-console-hang");
      const isThink = li.hasAttribute("data-think-mode");
      const isConsoleCall = li.classList.contains("is-console-call");
      const isConsoleGroup = !isThink && li.classList.contains("is-console-group");
      if (!reveal && !isThink && !isConsoleCall && !isConsoleGroup) return;
      if (isThink && li.dataset.thinkMode !== "collapsed" && String(window.getSelection() || "").trim()) return;
      event.preventDefault();
      const open = isThink
        ? ((li.dataset.thinkMode !== "expanded"))
        : isConsoleCall
          ? !hang?.classList.contains("is-expanded")
          : isConsoleGroup
            ? !li.classList.contains("is-console-expanded")
            : !reveal.classList.contains("is-open");
      if (open && !isThink) deps.pauseThreadFollow();
      if (isConsoleCall && hang) {
        hang.classList.toggle("is-expanded", open);
        li.classList.toggle("is-console-expanded", open);
      } else if (isConsoleGroup) {
        li.classList.toggle("is-console-expanded", open);
      } else if (reveal) {
        reveal.classList.toggle("is-revealing", open);
        deps.setToolReveal(reveal, open);
      }
      li.querySelectorAll(":scope > .chat-step-main > .chat-step-head [data-step-toggle]").forEach((el) => el.setAttribute("aria-expanded", String(open)));
      if (!msg.stepOpen) msg.stepOpen = {};
      msg.stepOpen[li.dataset.step] = open;
      if (isConsoleGroup) {
        consoleGroupOpenKeys(li, msg).forEach((key) => { msg.stepOpen[key] = open; });
      }
      if (isThink) {
        // A live thought opens in place so a pinned transcript stays on its last line.
        const liveThink = li.classList.contains("is-live-think");
        deps.syncThinkingMode(li, msg, !liveThink);
        const peek = li.querySelector("[data-reason-preview]");
        if (open && liveThink) {
          deps.snapThread();
          peek._thinking.follow = true;
          deps.followThinking(peek, true);
        } else if (open) {
          deps.stopThinkingFollow(peek);
          peek.scrollTop = 0;
        }
      }
      deps.saveDisclosures(msg);
    });
    deps.updateAssistant(article, msg, live);
    if (!live && !msg.noActions) deps.mountActions(article, deps.messages.indexOf(msg));
    return article;
  }

  return { consoleGroupOpenKeys, renderAssistant };
}
