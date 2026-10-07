(() => {
  const root = document.querySelector("[data-sched]");
  if (!root) return;
  const cfg = JSON.parse(document.getElementById("sched-config")?.textContent || "{}");
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const body = root.querySelector("[data-sched-body]");
  const empty = root.querySelector("[data-sched-empty]");
  const table = root.querySelector("[data-sched-table]");
  const err = root.querySelector("[data-page-error]");
  const modal = root.querySelector("[data-sched-modal]");
  const form = root.querySelector("[data-sched-form]");
  const formErr = root.querySelector("[data-sched-error]");
  const detail = root.querySelector("[data-sched-detail]");
  const detailTitle = root.querySelector("[data-detail-title]");
  const detailWhen = root.querySelector("[data-detail-when]");
  const detailFires = root.querySelector("[data-detail-fires]");
  const preview = root.querySelector("[data-f-preview]");
  let items = cfg.schedules || [];
  let editing = null;
  let openId = null;

  async function api(path, opts = {}) {
    const headers = { "X-CSRF-Token": csrf, ...(opts.headers || {}) };
    if (opts.body && !(opts.body instanceof FormData) && typeof opts.body !== "string") {
      headers["Content-Type"] = "application/json";
      opts = { ...opts, body: JSON.stringify(opts.body) };
    }
    const res = await fetch(path, { credentials: "same-origin", ...opts, headers });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = data?.error?.message || res.statusText || "Request failed";
      const error = new Error(msg);
      error.status = res.status;
      throw error;
    }
    return data;
  }

  function showError(node, error) {
    if (!node) return;
    node.hidden = !error;
    node.textContent = error ? String(error.message || error) : "";
  }

  function fmtWhen(iso) {
    if (!iso) return "—";
    const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
    if (!Number.isFinite(d.getTime())) return iso;
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function statusLabel(status) {
    const map = {
      running: "Running",
      completed: "Done",
      failed: "Failed",
      vault_locked: "Vault locked",
      skipped_overlap: "Skipped",
      skipped_capacity: "Busy",
      skipped_catchup: "Missed",
      queued: "Queued",
      cancelled: "Cancelled",
      interrupted: "Interrupted",
    };
    return map[status] || status || "—";
  }

  function targetLabel(item) {
    if (item.target === "project") {
      const name = item.project_name || "Project";
      return item.host_alias ? `${name} · ${item.host_alias}` : name;
    }
    return "Chat";
  }

  function chatHref(threadId) {
    return threadId ? `/chat?t=${encodeURIComponent(threadId)}` : "";
  }

  function render() {
    if (!body) return;
    if (!items.length) {
      if (table) table.hidden = true;
      if (empty) empty.hidden = false;
      body.innerHTML = "";
      return;
    }
    if (table) table.hidden = false;
    if (empty) empty.hidden = true;
    body.innerHTML = items.map((item) => {
      const href = chatHref(item.thread_id);
      const fault = item.last_status === "failed" || item.last_status === "vault_locked";
      const last = item.last_status
        ? `<span class="sched-status is-${escapeAttr(item.last_status)}"${item.last_error ? ` title="${escapeAttr(item.last_error)}"` : ""}>${escapeHtml(statusLabel(item.last_status))}</span><span class="sched-when">${escapeHtml(fmtWhen(item.last_fire_at))}</span>`
        : `<span class="sched-muted">Never fired</span>`;
      const open = href
        ? `<a class="sched-open" href="${href}">Open chat</a>`
        : `<span class="sched-muted">No chat yet</span>`;
      const paused = item.paused || !item.enabled ? `<span class="sched-pill">${item.paused ? "Paused" : "Off"}</span>` : "";
      return `<tr data-id="${escapeAttr(item.id)}" class="${fault ? "is-fault" : ""}${openId === item.id ? " is-open" : ""}">
        <td><div class="sched-line">
            <button type="button" class="sched-name" data-open title="${escapeAttr(item.name)}">${escapeHtml(item.name)}</button>
            <button type="button" class="sched-edit" data-edit>Edit</button>${paused}
        </div></td>
        <td title="${escapeAttr(targetLabel(item))}">${escapeHtml(targetLabel(item))}</td>
        <td title="${escapeAttr(item.when || "")}">${escapeHtml(item.when || "")}</td>
        <td>${last}</td>
        <td><div class="sched-line"><span class="sched-when">${escapeHtml(fmtWhen(item.next_fire_at))}</span>${open}</div></td>
      </tr>`;
    }).join("");
  }

  function escapeHtml(value) {
    return escapeText(value);
  }
  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, "&#96;");
  }

  function fillSelects() {
    const models = form.querySelector("[data-f-model]");
    const projects = form.querySelector("[data-f-project]");
    if (models) {
      models.innerHTML = (cfg.models || []).map((m) => `<option value="${escapeAttr(m.id)}">${escapeHtml(m.name)}</option>`).join("");
    }
    if (projects) {
      projects.innerHTML = (cfg.projects || []).map((p) => `<option value="${escapeAttr(p.id)}">${escapeHtml(p.name)} · ${escapeHtml(p.host_alias)}</option>`).join("");
    }
  }

  function builderState() {
    const kind = form.querySelector("[data-f-builder]").value;
    form.querySelector("[data-f-time-wrap]").hidden = !["daily", "weekly"].includes(kind);
    form.querySelector("[data-f-every-wrap]").hidden = kind !== "every";
    form.querySelector("[data-f-once-wrap]").hidden = kind !== "once";
    form.querySelector("[data-f-cron-wrap]").hidden = kind !== "cron";
    form.querySelector("[data-f-dows]").hidden = kind !== "weekly";
    const target = form.querySelector("[data-f-target]").value;
    form.querySelector("[data-f-project-wrap]").hidden = target !== "project";
    form.querySelector("[data-f-coding-wrap]").hidden = target === "project";
    const codingOn = target === "project" || form.querySelector("[data-f-coding]").checked;
    const note = form.querySelector("[data-f-coding-note]");
    if (note) note.hidden = !codingOn;
  }

  function weekdays() {
    let mask = 0;
    form.querySelectorAll("[data-f-dows] input:checked").forEach((el) => {
      mask |= 1 << Number(el.value);
    });
    return mask;
  }

  function payload() {
    const builder = form.querySelector("[data-f-builder]").value;
    const target = form.querySelector("[data-f-target]").value;
    const data = {
      name: form.querySelector("[data-f-name]").value.trim(),
      model: form.querySelector("[data-f-model]").value,
      target,
      prompt: form.querySelector("[data-f-prompt]").value,
      timezone: form.querySelector("[data-f-tz]").value.trim() || "UTC",
      allow_coding: form.querySelector("[data-f-coding]").checked,
      fresh_each_fire: form.querySelector("[data-f-fresh]").checked,
      tools: ["web_search"],
    };
    if (target === "project") data.project_id = Number(form.querySelector("[data-f-project]").value);
    if (builder === "daily") {
      data.recur_kind = "cron";
      data.builder = "daily";
      data.at_local = form.querySelector("[data-f-time]").value || "09:00";
    } else if (builder === "weekly") {
      data.recur_kind = "cron";
      data.builder = "weekly";
      data.at_local = form.querySelector("[data-f-time]").value || "09:00";
      data.weekdays = weekdays();
    } else if (builder === "every") {
      data.recur_kind = "every";
      data.every_seconds = Number(form.querySelector("[data-f-every]").value);
    } else if (builder === "once") {
      data.recur_kind = "once";
      data.once_at = form.querySelector("[data-f-once]").value.trim();
    } else {
      data.recur_kind = "cron";
      data.cron_expr = form.querySelector("[data-f-cron]").value.trim();
    }
    return data;
  }

  let previewSeq = 0;
  async function refreshPreview() {
    if (!preview) return;
    // Latest edit wins: an older response must not overwrite a newer one.
    const seq = ++previewSeq;
    const name = form.querySelector("[data-f-name]").value.trim();
    const prompt = form.querySelector("[data-f-prompt]").value.trim();
    if (!name || !prompt) {
      preview.textContent = "";
      return;
    }
    try {
      const data = await api("/schedules/preview", { method: "POST", body: payload() });
      if (seq !== previewSeq) return;
      const next = (data.preview || []).slice(0, 5).map(fmtWhen).join(" · ");
      preview.textContent = next ? `Next: ${next}` : data.when || "";
    } catch (error) {
      if (seq !== previewSeq) return;
      preview.textContent = error.message || "";
    }
  }

  function openModal(item) {
    editing = item || null;
    form.reset();
    showError(formErr, "");
    fillSelects();
    root.querySelector("[data-sched-run]").hidden = !item;
    root.querySelector("[data-sched-delete]").hidden = !item;
    document.getElementById("sched-title").textContent = item ? "Edit task" : "New task";
    if (item) {
      form.querySelector("[data-f-name]").value = item.name || "";
      form.querySelector("[data-f-model]").value = item.model || "";
      form.querySelector("[data-f-target]").value = item.target || "chat";
      if (item.project_id) form.querySelector("[data-f-project]").value = String(item.project_id);
      form.querySelector("[data-f-prompt]").value = item.prompt || "";
      form.querySelector("[data-f-tz]").value = item.timezone || "UTC";
      form.querySelector("[data-f-coding]").checked = Boolean(item.allow_coding);
      form.querySelector("[data-f-fresh]").checked = Boolean(item.fresh_each_fire);
      if (item.recur_kind === "every") {
        form.querySelector("[data-f-builder]").value = "every";
        form.querySelector("[data-f-every]").value = String(item.every_seconds || 3600);
      } else if (item.recur_kind === "once") {
        form.querySelector("[data-f-builder]").value = "once";
        form.querySelector("[data-f-once]").value = item.once_at || "";
      } else if (item.weekdays) {
        form.querySelector("[data-f-builder]").value = "weekly";
        form.querySelector("[data-f-time]").value = item.at_local || "09:00";
        form.querySelectorAll("[data-f-dows] input").forEach((el) => {
          el.checked = Boolean(item.weekdays & (1 << Number(el.value)));
        });
      } else if (item.at_local) {
        form.querySelector("[data-f-builder]").value = "daily";
        form.querySelector("[data-f-time]").value = item.at_local;
      } else {
        form.querySelector("[data-f-builder]").value = "cron";
        form.querySelector("[data-f-cron]").value = item.cron_expr || "";
      }
    }
    builderState();
    modal.showModal();
    refreshPreview();
  }

  function renderDetail(item, fires) {
    if (!detail) return;
    detail.hidden = false;
    detailTitle.textContent = item.name;
    detailWhen.textContent = `${item.when || ""} · next ${fmtWhen(item.next_fire_at)}`;
    detailFires.innerHTML = (fires || []).map((row) => {
      const href = chatHref(row.thread_id);
      const err = row.error
        ? `<span class="sched-err" title="${escapeAttr(row.error)}">${escapeHtml(row.error)}</span>`
        : "";
      const fault = row.status === "failed" || row.status === "vault_locked";
      return `<tr class="${fault ? "is-fault" : ""}">
        <td><span class="sched-when">${escapeHtml(fmtWhen(row.started_at))}</span></td>
        <td><div class="sched-line"><span class="sched-status is-${escapeAttr(row.status)}">${escapeHtml(statusLabel(row.status))}</span>${err}</div></td>
        <td>${href ? `<a class="sched-open" href="${href}">Open chat</a>` : `<span class="sched-muted">—</span>`}</td>
      </tr>`;
    }).join("") || `<tr><td colspan="3" class="sched-muted">No fires yet.</td></tr>`;
  }

  async function refreshDetail(item) {
    try {
      const data = await api("/schedules/" + encodeURIComponent(item.id));
      const fresh = items.find((s) => s.id === item.id);
      if (fresh) Object.assign(fresh, data);
      if (openId === item.id) {
        renderDetail(fresh || item, data.fires || []);
        render();
      }
    } catch (_) {}
  }

  async function toggleDetail(item) {
    if (openId === item.id) {
      openId = null;
      if (detail) detail.hidden = true;
      render();
      return;
    }
    openId = item.id;
    render();
    if (detail) detailFires.innerHTML = `<tr><td colspan="3" class="sched-muted">Loading…</td></tr>`;
    await refreshDetail(item);
  }

  root.querySelector("[data-sched-new]")?.addEventListener("click", () => openModal(null));
  document.querySelector("[data-sched-new]")?.addEventListener("click", () => openModal(null));
  root.querySelectorAll("[data-sched-close]").forEach((btn) => btn.addEventListener("click", () => modal.close()));
  // `change` bubbles to the form listener below, which refreshes the preview.
  form.querySelector("[data-f-builder]").addEventListener("change", () => builderState());
  form.querySelector("[data-f-target]").addEventListener("change", () => builderState());
  form.querySelector("[data-f-coding]").addEventListener("change", () => builderState());
  form.addEventListener("change", () => refreshPreview());
  body?.addEventListener("click", async (event) => {
    const row = event.target.closest("tr[data-id]");
    if (!row) return;
    const item = items.find((s) => s.id === row.getAttribute("data-id"));
    if (!item) return;
    if (event.target.closest("a")) return;
    if (event.target.closest("[data-edit]")) {
      openModal(item);
      return;
    }
    if (event.target.closest("[data-open]") && item.thread_id) {
      window.location.href = chatHref(item.thread_id);
      return;
    }
    await toggleDetail(item);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showError(formErr, "");
    try {
      const data = payload();
      const saved = editing
        ? await api("/schedules/" + encodeURIComponent(editing.id), { method: "PATCH", body: data })
        : await api("/schedules", { method: "POST", body: data });
      const idx = items.findIndex((s) => s.id === saved.id);
      if (idx >= 0) items[idx] = { ...items[idx], ...saved };
      else items.unshift(saved);
      render();
      modal.close();
    } catch (error) {
      showError(formErr, error);
    }
  });

  root.querySelector("[data-sched-run]")?.addEventListener("click", async () => {
    if (!editing) return;
    showError(formErr, "");
    try {
      const saved = await api("/schedules/" + encodeURIComponent(editing.id) + "/run", { method: "POST", body: {} });
      const idx = items.findIndex((s) => s.id === saved.id);
      if (idx >= 0) items[idx] = { ...items[idx], ...saved };
      render();
      if (saved.thread_id) window.location.href = chatHref(saved.thread_id);
    } catch (error) {
      showError(formErr, error);
    }
  });

  root.querySelector("[data-sched-delete]")?.addEventListener("click", async () => {
    if (!editing) return;
    if (!window.confirm("Delete this task? Existing chats stay.")) return;
    try {
      await api("/schedules/" + encodeURIComponent(editing.id), { method: "DELETE" });
      items = items.filter((s) => s.id !== editing.id);
      if (openId === editing.id) {
        openId = null;
        if (detail) detail.hidden = true;
      }
      render();
      modal.close();
    } catch (error) {
      showError(formErr, error);
    }
  });

  fillSelects();
  render();

  async function poll() {
    if (document.hidden) return;
    try {
      const data = await api("/schedules");
      if (Array.isArray(data.schedules)) items = data.schedules;
      render();
      const open = items.find((s) => s.id === openId);
      if (open) await refreshDetail(open);
    } catch (_) {}
  }
  setInterval(poll, 15000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });
})();
