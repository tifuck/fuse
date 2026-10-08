// todos presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function todoLineHtml(text) {
    return `<p class="chat-todo-line" data-todo-line><span class="chat-todo-line-mark" aria-hidden="true"></span><span class="chat-todo-line-text">${deps.escapeHtml(text)}</span></p>`;
  }

// The tray's frame is built once so the sheet can slide open and shut and the
// tab can pulse; only the tab's contents and the rows are repainted. The tab
// rides on the sheet's top edge, like the tab of a page.
function todoTrayHtml(listId) {
    return '<div class="chat-todo-sheet"><div class="chat-todo-bar" data-todo-bar></div>' +
      `<div class="chat-todo-drawer"><div class="chat-todo-clip"><div class="chat-todo-page"><ol class="chat-todo-rows" id="${listId}" data-todo-list></ol></div></div></div></div>`;
  }

function todoBarHtml(state, { active, expanded, listId }) {
    const items = state.items;
    const done = items.filter(deps.todoClosed).length;
    const dismiss = active ? "" : '<button type="button" class="chat-todo-dismiss" data-todo-dismiss aria-label="Dismiss todo list" title="Dismiss">×</button>';
    return (
      `<button type="button" class="chat-todo-head" data-todo-toggle aria-expanded="${expanded ? "true" : "false"}" aria-controls="${listId}" title="${expanded ? "Hide todos" : "Show todos"}">` +
      `<span class="chat-todo-title">Todos</span><span class="chat-todo-count" data-todo-count>${done}/${items.length}</span>` +
      '<svg class="chat-todo-chevron" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M4 10l4-4 4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
      `</button>${dismiss}`
    );
  }

function todoRowsHtml(items) {
    return items.map((item) =>
      `<li class="chat-todo-row is-${item.status.replace("_", "-")}"><span class="chat-todo-box" aria-hidden="true"></span>` +
      `<span class="chat-todo-text">${deps.escapeHtml(item.content)}</span></li>`).join("");
  }

  return { todoLineHtml, todoTrayHtml, todoBarHtml, todoRowsHtml };
}
