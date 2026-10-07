// message-actions presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function msgActions(index, role) {
    const bar = document.createElement("div");
    bar.className = "chat-msg-actions";
    const add = (label, svg, act) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-icon-btn";
      btn.title = label;
      btn.setAttribute("aria-label", label);
      btn.innerHTML = svg;
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        // Indices address the complete transcript; wait until it has loaded.
        if (deps.partialTranscript) return;
        act(btn);
      });
      bar.appendChild(btn);
    };
    add("Copy", deps.ACTION_ICONS.copy, (btn) => {
      const msg = deps.messages[index];
      deps.copyText(msg ? deps.rewriteCites(msg.content, deps.sourceCatalog(msg), false) : "")
        .then(() => {
          btn.innerHTML = deps.ACTION_ICONS.copied;
          btn.setAttribute("aria-label", "Copied");
          setTimeout(() => {
            btn.innerHTML = deps.ACTION_ICONS.copy;
            btn.setAttribute("aria-label", "Copy");
          }, 1200);
        })
        .catch(() => {});
    });
    if (role === "user") add("Edit", deps.ACTION_ICONS.edit, () => deps.beginEdit(index));
    if (role === "user") add("Rewind", deps.ACTION_ICONS.rewind, () => deps.rewindTo(index));
    if (role === "assistant" && index === deps.messages.length - 1) add("Retry", deps.ACTION_ICONS.retry, () => deps.retryAssistant(index));
    add("More", deps.ACTION_ICONS.more, (btn) => {
      const items = [
        { label: "Delete", danger: true, run: () => deps.confirmDeleteAt(index, "one") },
        { label: "Delete from here", danger: true, run: () => deps.confirmDeleteAt(index, "from") },
        { label: "Keep up to here", danger: true, run: () => deps.confirmDeleteAt(index, "after") },
      ];
      if (deps.messages.length || deps.undoStack.length || deps.redoStack.length) items.push({ sep: true });
      if (deps.messages.length || deps.undoStack.length) items.push({ label: "Undo last turn", run: () => deps.undoConversation() });
      if (deps.redoStack.length) items.push({ label: "Redo", run: () => deps.redoConversation() });
      deps.showPop(btn, items);
    });
    return bar;
  }

function mountActions(article, index) {
    const host = article.querySelector("[data-actions-host]");
    if (!host || index < 0) return;
    const key = index + ":" + (index === deps.messages.length - 1);
    if (host.childElementCount && article._actionKey === key) return;
    host.replaceChildren(msgActions(index, "assistant"));
    article._actionKey = key;
  }

  return { msgActions, mountActions };
}
