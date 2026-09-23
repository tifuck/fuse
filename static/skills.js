(() => {
  const root = document.querySelector("[data-skills]");
  if (!root) return;
  const cfgEl = document.getElementById("skills-config");
  let bundle = { skills: [], coding: false };
  try {
    bundle = JSON.parse(cfgEl ? cfgEl.textContent : "{}") || bundle;
  } catch (_) {}

  function csrf() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute("content") : "";
  }

  async function api(path, opts) {
    const headers = { "X-CSRF-Token": csrf(), Accept: "application/json" };
    if (opts && opts.body && !(opts.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }
    const res = await fetch(path, { ...opts, headers, credentials: "same-origin" });
    const text = await res.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_) {
      data = { error: { message: text || res.statusText } };
    }
    if (!res.ok) {
      const errBody = data.error || {};
      const msg =
        (typeof errBody.message === "string" && errBody.message) ||
        (typeof data.detail === "string" && data.detail) ||
        res.statusText;
      const err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  const list = root.querySelector("[data-skill-list]");
  const empty = root.querySelector("[data-empty]");
  const pageError = root.querySelector("[data-page-error]");
  const searchForm = root.querySelector("[data-search-form]");
  const searchQ = root.querySelector("[data-search-q]");
  const searchError = root.querySelector("[data-search-error]");
  const searchResults = root.querySelector("[data-search-results]");
  const uploadForm = root.querySelector("[data-upload-form]");
  const uploadFile = root.querySelector("[data-upload-file]");
  const uploadError = root.querySelector("[data-upload-error]");
  const ORIGIN = { bundled: "bundled", upload: "upload", github: "github", skills_sh: "skills.sh", chat: "chat" };

  function showErr(el, msg) {
    if (!el) return;
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = msg;
  }

  function originLabel(origin) {
    return ORIGIN[origin] || origin || "bundled";
  }

  function renderList() {
    const items = bundle.skills || [];
    list.innerHTML = "";
    empty.hidden = items.length > 0;
    items.forEach((skill) => {
      const row = document.createElement("div");
      row.className = "skills-row";
      const slug = document.createElement("code");
      slug.className = "skills-slug";
      slug.textContent = skill.slug;
      const desc = document.createElement("p");
      desc.className = "skills-desc";
      desc.textContent = skill.description || "";
      const badge = document.createElement("span");
      badge.className = "skills-origin";
      badge.textContent = originLabel(skill.origin);
      const toggle = document.createElement("label");
      toggle.className = "skills-toggle";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = Boolean(skill.enabled);
      box.addEventListener("change", async () => {
        showErr(pageError, "");
        try {
          const updated = await api("/skills/" + encodeURIComponent(skill.slug), {
            method: "PATCH",
            body: JSON.stringify({ enabled: box.checked }),
          });
          Object.assign(skill, updated);
          renderList();
        } catch (err) {
          box.checked = Boolean(skill.enabled);
          showErr(pageError, err.message);
        }
      });
      const lab = document.createElement("span");
      lab.textContent = "On";
      toggle.append(box, lab);
      const actions = document.createElement("div");
      actions.className = "skills-row-actions";
      actions.append(toggle);
      if (skill.can_remove && skill.origin !== "bundled") {
        const del = document.createElement("button");
        del.type = "button";
        del.className = "btn sm ghost";
        del.textContent = "Remove";
        del.addEventListener("click", async () => {
          showErr(pageError, "");
          try {
            await api("/skills/" + encodeURIComponent(skill.slug), { method: "DELETE" });
            bundle = await api("/skills/api");
            renderList();
          } catch (err) {
            showErr(pageError, err.message);
          }
        });
        actions.append(del);
      }
      const meta = document.createElement("div");
      meta.className = "skills-row-meta";
      meta.append(slug, badge);
      const copy = document.createElement("div");
      copy.className = "skills-row-copy";
      copy.append(meta, desc);
      row.append(copy, actions);
      list.append(row);
    });
  }

  function hitKey(hit) {
    return hit.id || (hit.source ? hit.source + "/" + (hit.skillId || "") : "");
  }

  function installedKeys() {
    return new Set((bundle.skills || []).map((s) => s.source_id || "").filter(Boolean));
  }

  function setBtnState(btn, state) {
    if (state === "busy") {
      btn.disabled = true;
      btn.classList.add("is-busy");
      btn.classList.remove("is-done");
      btn.setAttribute("aria-busy", "true");
      btn.innerHTML = '<span class="spin" aria-hidden="true"></span>';
      btn.title = "Installing…";
      return;
    }
    btn.removeAttribute("aria-busy");
    btn.title = "";
    if (state === "done") {
      btn.disabled = true;
      btn.classList.remove("is-busy");
      btn.classList.add("is-done");
      btn.style.minWidth = "";
      btn.textContent = "Installed";
      return;
    }
    btn.disabled = false;
    btn.classList.remove("is-busy", "is-done");
    btn.style.minWidth = "";
    btn.textContent = "Install";
  }

  function renderHits(hits) {
    searchResults.innerHTML = "";
    const installed = installedKeys();
    (hits || []).forEach((hit) => {
      const row = document.createElement("div");
      row.className = "skills-hit";
      const name = document.createElement("strong");
      name.textContent = hit.name || hit.skillId;
      const src = document.createElement("span");
      src.className = "mono";
      src.textContent = hit.source || "";
      const installs = document.createElement("span");
      installs.className = "skills-installs";
      installs.textContent = (hit.installs || 0).toLocaleString() + " installs";
      const link = document.createElement("a");
      link.href = hit.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "skills.sh";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn sm";
      const key = hitKey(hit);
      if (installed.has(key)) setBtnState(btn, "done");
      else btn.textContent = "Install";
      btn.addEventListener("click", async () => {
        if (btn.disabled) return;
        showErr(searchError, "");
        btn.style.minWidth = btn.offsetWidth + "px";
        setBtnState(btn, "busy");
        try {
          const row = await api("/skills/install", {
            method: "POST",
            body: JSON.stringify({
              id: hit.id,
              source: hit.source,
              skill_id: hit.skillId,
            }),
          });
          if (row && row.source_id) installed.add(row.source_id);
          setBtnState(btn, "done");
          bundle = await api("/skills/api");
          renderList();
        } catch (err) {
          setBtnState(btn, "idle");
          showErr(searchError, err.message);
        }
      });
      row.append(name, src, installs, link, btn);
      searchResults.append(row);
    });
  }

  searchForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    showErr(searchError, "");
    const q = (searchQ.value || "").trim();
    if (q.length < 2) {
      showErr(searchError, "Type at least two characters.");
      return;
    }
    try {
      const data = await api("/skills/catalog?q=" + encodeURIComponent(q));
      renderHits(data.skills || []);
      if (!(data.skills || []).length) showErr(searchError, "No GitHub skills matched.");
    } catch (err) {
      showErr(searchError, err.message);
    }
  });

  uploadForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    showErr(uploadError, "");
    const file = uploadFile && uploadFile.files && uploadFile.files[0];
    if (!file) {
      showErr(uploadError, "Choose a .zip or SKILL.md.");
      return;
    }
    const body = new FormData();
    body.append("file", file);
    try {
      await api("/skills/upload", { method: "POST", body });
      uploadFile.value = "";
      bundle = await api("/skills/api");
      renderList();
    } catch (err) {
      showErr(uploadError, err.message);
    }
  });

  renderList();
})();
