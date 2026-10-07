// user-turn presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function consoleWakeNoticeHtml(trace) {
    const monitor = trace.name === "monitor";
    const label = deps.ccFlat(trace.description) || (monitor ? deps.ccFlat(trace.url) : "") || deps.ccFlat(deps.displayCommand(trace)) || "command";
    const state = deps.executionState(trace);
    const exit = deps.shellExitCode(trace);
    const stopped = trace.status === "interrupted" || ["cancelled", "interrupted", "lost"].includes(state);
    let text;
    let tone = "dim";
    if (monitor) {
      const events = Number(trace.event_count) || 0;
      if (stopped || state === "timed_out") { text = `Monitor "${label}" stopped`; tone = "gold"; }
      else if (trace.status === "done") {
        text = events ? `Monitor "${label}" stream ended` : `Monitor "${label}" ended without producing output`;
        tone = "green";
      }
      else { text = `Monitor "${label}" script failed${exit !== null ? ` (exit ${exit})` : ""}`; tone = "red"; }
    } else if (stopped) {
      text = `Background command "${label}" was stopped`;
      tone = "gold";
    } else if (trace.status === "done") {
      // Console prints the exit code even when it is 0, on a finished (white) row.
      text = `Background command "${label}" completed${exit !== null && (exit !== 0 || true) ? ` (exit code ${exit})` : ""}`;
      tone = "green";
    } else {
      text = `Background command "${label}" failed${exit !== null ? ` with exit code ${exit}` : ""}`;
      tone = "red";
    }
    const took = Number(trace.latency_ms ?? trace.duration_ms);
    const detail = Number.isFinite(took) && took > 0 ? ` · ${deps.ccDuration(Math.max(1000, took))}` : "";
    const body = !monitor && typeof trace.output === "string" ? deps.cleanToolOutput(trace, trace.output).replace(/\s+$/, "") : "";
    const output = body ? `<div class="chat-console-hang is-expanded"><span class="chat-console-branch" aria-hidden="true">└</span><div class="chat-console-hang-body">${deps.consoleStdoutHtml(body.split("\n").slice(0, 4))}</div></div>` : "";
    return (
      `<li class="chat-step is-console-notice is-${tone}" data-execution="${deps.escapeHtml(deps.executionIdentity(trace))}">` +
      `<span class="chat-step-node" aria-hidden="true"></span>` +
      `<div class="chat-step-main"><span class="chat-console-notice-text">${deps.escapeHtml(text)}` +
      (detail ? `<span class="chat-console-dim">${deps.escapeHtml(detail)}</span>` : "") + `</span>${output}</div></li>`
    );
  }

function renderAutoSteps(msg) {
    const article = document.createElement("article");
    article.className = "chat-turn is-user is-auto-steps";
    article.setAttribute("aria-label", "Background command results");
    const steps = (deps.consoleAutoStepsHtml(msg));
    article.innerHTML = '<div class="chat-auto-steps"><ol class="chat-timeline">' + steps + "</ol></div>";
    article.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-step-toggle]");
      if (!btn || event.target.closest(".chat-step-open")) return;
      const li = btn.closest("[data-step]");
      if (!li) return;
      const reveal = li.querySelector(":scope > .chat-step-main > .chat-step-reveal");
      if (!reveal) return;
      event.preventDefault();
      const open = !reveal.classList.contains("is-open");
      deps.setToolReveal(reveal, open);
      li.querySelectorAll(":scope > .chat-step-main > .chat-step-head [data-step-toggle]").forEach((el) => el.setAttribute("aria-expanded", String(open)));
    });
    return article;
  }

function renderUser(msg, index, live) {
    const article = document.createElement("article");
    article.className = "chat-turn is-user";
    article.setAttribute("aria-label", "Your message");
    if (msg.fromParent) {
      // A parent follow-up inside a subagent panel: a real message, not an
      // automated-event chip. Queued ones say they are still being delivered.
      article.classList.add("is-from-agent");
      article.setAttribute("aria-label", msg.pending ? "Message from the main agent, awaiting delivery" : "Message from the main agent");
      const tag = document.createElement("div");
      tag.className = "chat-agent-note";
      tag.textContent = msg.pending ? "From main agent · delivering at the next step" : "From main agent";
      const bubble = document.createElement("div");
      bubble.className = "chat-bubble";
      bubble.textContent = msg.content;
      article.append(tag, bubble);
      return article;
    }
    if (msg.auto) {
      if (Array.isArray(msg.autoSteps) && msg.autoSteps.length) {
        return renderAutoSteps(msg);
      }
      const label = deps.autoEventLabel(msg.content);
      article.classList.add("is-auto");
      article.setAttribute("aria-label", label);
      article.innerHTML =
        '<button type="button" class="chat-auto-chip" data-auto-toggle aria-expanded="false">' +
        '<span class="chat-auto-dot" aria-hidden="true"></span>' +
        '<span class="chat-auto-label">' + deps.escapeHtml(label) + "</span>" +
        STEP_CHEVRON +
        "</button>" +
        '<div class="chat-auto-reveal"><div class="chat-auto-reveal-inner"><pre>' + deps.escapeHtml(msg.content) + "</pre></div></div>";
      return article;
    }
    if (deps.editingIndex === index) {
      const wrap = document.createElement("div");
      wrap.className = "chat-edit";
      const area = document.createElement("textarea");
      area.dataset.edit = "1";
      area.setAttribute("aria-label", "Edit message");
      area.value = msg.content;
      const actions = document.createElement("div");
      actions.className = "chat-edit-actions";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "btn ghost sm";
      cancel.textContent = "Cancel";
      cancel.addEventListener("click", () => {
        deps.editingIndex = -1;
        deps.editingFiles = [];
        deps.paint();
      });
      const save = document.createElement("button");
      save.type = "button";
      save.className = "btn sm";
      save.textContent = "Save & resend";
      save.addEventListener("click", () => deps.commitEdit(index, area.value));
      area.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          deps.editingIndex = -1;
          deps.editingFiles = [];
          deps.paint();
        } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
          event.preventDefault();
          deps.commitEdit(index, area.value);
        }
      });
      actions.appendChild(cancel);
      actions.appendChild(save);
      wrap.appendChild(area);
      if (deps.editingFiles.length) wrap.appendChild(deps.editFilesStrip());
      wrap.appendChild(actions);
      article.appendChild(wrap);
      return article;
    }
    deps.fillUserFiles(article, msg, index);
    if (msg.content) {
      const bubble = document.createElement("div");
      bubble.className = "chat-bubble";
      bubble.textContent = msg.content;
      article.appendChild(bubble);
    }
    if (!live && !msg.noActions) article.appendChild(deps.msgActions(index, "user"));
    return article;
  }

function fileExtLabel(name) {
    const match = String(name || "").match(/\.([a-z0-9]{1,5})$/i);
    return match ? match[1].toUpperCase() : "";
  }

function imagePreviewButton(att) {
    const name = att.name || "";
    return `<button type="button" class="chat-user-file-view" data-image-preview="${deps.escapeHtml(att.data)}" data-image-name="${deps.escapeHtml(name)}" aria-label="Preview ${deps.escapeHtml(name || "image")} full screen"><img src="${deps.escapeHtml(att.data)}" alt="${deps.escapeHtml(name || "Image")}" loading="lazy"></button>`;
  }

const STEP_CHEVRON =
    '<svg class="chat-step-chevron" viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M5 6.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  return { consoleWakeNoticeHtml, renderAutoSteps, renderUser, fileExtLabel, imagePreviewButton, STEP_CHEVRON };
}
