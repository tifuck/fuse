// todos presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function todoLineHtml(text) {
    return `<p class="chat-todo-line" data-todo-line><span class="chat-todo-line-mark" aria-hidden="true"></span><span class="chat-todo-line-text">${deps.escapeHtml(text)}</span></p>`;
  }

function todoPanelHtml(state, { active, expanded }) {
    const items = state.items;
    const done = items.filter(deps.todoClosed).length;
    let rows = items.map((item) => ({ item }));
    let hiddenDone = 0;
    let hiddenMore = 0;
    const folds = items.length > deps.TODO_ROWS;
    if (folds && !expanded) {
      for (let i = 0; i < rows.length && rows.length > deps.TODO_ROWS;) {
        if (deps.todoClosed(rows[i].item)) { rows.splice(i, 1); hiddenDone += 1; } else i += 1;
      }
      while (rows.length > deps.TODO_ROWS) {
        let k = rows.length - 1;
        while (k > 0 && rows[k].item.status === "in_progress") k -= 1;
        rows.splice(k, 1);
        hiddenMore += 1;
      }
    }
    const count = `<span class="chat-todo-count">${done}/${items.length}</span>`;
    const title = `<span class="chat-todo-title">Todos</span>${count}`;
    const head = folds
      ? `<button type="button" class="chat-todo-head" data-todo-toggle aria-expanded="${expanded ? "true" : "false"}">${title}<span class="chat-todo-fold" aria-hidden="true">${expanded ? "less" : "all"}</span></button>`
      : `<div class="chat-todo-head">${title}</div>`;
    const dismiss = active ? "" : '<button type="button" class="chat-todo-dismiss" data-todo-dismiss aria-label="Dismiss todo list" title="Dismiss">×</button>';
    const list = rows.map(({ item }) =>
      `<li class="chat-todo-row is-${item.status.replace("_", "-")}"><span class="chat-todo-box" aria-hidden="true"></span>` +
      `<span class="chat-todo-text">${deps.escapeHtml(item.content)}</span></li>`);
    if (hiddenDone) list.unshift(`<li class="chat-todo-row is-more"><span class="chat-todo-box" aria-hidden="true"></span><span class="chat-todo-text">${hiddenDone} done</span></li>`);
    if (hiddenMore) list.push(`<li class="chat-todo-row is-more"><span class="chat-todo-box" aria-hidden="true"></span><span class="chat-todo-text">+${hiddenMore} more</span></li>`);
    return `<div class="chat-todo-bar">${head}${dismiss}</div><ol class="chat-todo-rows" data-todo-list>${list.join("")}</ol>`;
  }

  return { todoLineHtml, todoPanelHtml };
}
