// todos presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function todoLineHtml(text) {
    return `<p class="chat-todo-line" data-todo-line><span class="chat-todo-line-mark" aria-hidden="true"></span><span class="chat-todo-line-text">${deps.escapeHtml(text)}</span></p>`;
  }

// The tray's frame is built once so the sheet can slide open and shut and the
// head line can pulse; only the head's contents and the rows are repainted.
// The header's todo button opens it.
function todoTrayHtml(listId) {
    return '<div class="chat-todo-sheet"><div class="chat-todo-drawer"><div class="chat-todo-clip"><div class="chat-todo-page">' +
      `<div class="chat-todo-bar" data-todo-bar></div><ol class="chat-todo-rows" id="${listId}" data-todo-list></ol></div></div></div></div>`;
  }

function todoBarHtml(state, { active }) {
    const items = state.items;
    const done = items.filter(deps.todoClosed).length;
    const dismiss = active ? "" : '<button type="button" class="chat-todo-dismiss" data-todo-dismiss aria-label="Dismiss todo list" title="Dismiss">×</button>';
    return `<span class="chat-todo-head"><span class="chat-todo-title">Todos</span><span class="chat-todo-count" data-todo-count>${done}/${items.length}</span></span>${dismiss}`;
  }

function todoRowsHtml(items) {
    return items.map((item) =>
      `<li class="chat-todo-row is-${item.status.replace("_", "-")}"><span class="chat-todo-box" aria-hidden="true"></span>` +
      `<span class="chat-todo-text">${deps.escapeHtml(item.content)}</span></li>`).join("");
  }

  return { todoLineHtml, todoTrayHtml, todoBarHtml, todoRowsHtml };
}
