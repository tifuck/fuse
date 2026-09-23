(() => {
  "use strict";
  const lab = document.querySelector(".motion-lab");
  if (!lab) return;
  // These ids mirror ACTIVITY_SPINNERS in static/chat.js — the subset of
  // Collection 03 artwork the live rotation actually uses, in the same order.
  const ROTATION_03_IDS = [
    "ink-runner", "time-turn", "time-exchange", "cardan-precession", "cardan-bands",
    "cardan-sphere", "portal-chase", "portal-corners", "solar-iris", "solar-wedges",
    "solar-turbine", "fluid-eight", "elastic-orbit", "braided-current", "shutter-drive",
  ];
  const groups = [
    {
      id: "05",
      storage: 5,
      label: "Collection 05 · New originals",
      studies: window.FuseSpinnerFuture || [],
    },
    {
      id: "03",
      storage: 3,
      label: "Collection 03",
      studies: ROTATION_03_IDS
        .map(id => (window.FuseSpinnerLoaders || []).find(study => study.id === id))
        .filter(Boolean),
    },
    {
      id: "04",
      storage: 4,
      label: "Collection 04",
      studies: window.FuseSpinnerNext || [],
    },
  ].filter(group => group.studies.length);
  if (!groups.length) return;
  const total = groups.reduce((sum, group) => sum + group.studies.length, 0);
  lab.querySelector("[data-ml-eyebrow]").textContent = `${total} loading icons · the live rotation`;
  lab.querySelector(".ml-table caption").textContent = `${total} spinner concepts currently in the live chat rotation, with enlarged, monochrome, and actual-size previews. Check Keep to shortlist a design.`;
  const selection = lab.querySelector("[data-ml-selection]");
  const copy = lab.querySelector("[data-ml-copy]");
  const clear = lab.querySelector("[data-ml-clear]");
  const filter = lab.querySelector("[data-ml-filter]");
  const pause = lab.querySelector("[data-ml-pause]");
  const surface = lab.querySelector("[data-ml-surface]");
  const feedback = lab.querySelector("[data-ml-feedback]");
  const motionQuery = matchMedia("(prefers-reduced-motion: reduce)");
  let paused = motionQuery.matches;
  let motionChosen = false;

  function savedIds(storageId) {
    try {
      const value = JSON.parse(localStorage.getItem(`fuse-motion-shortlist-v${storageId}`) || "[]");
      return Array.isArray(value) ? value.filter(id => typeof id === "string") : [];
    } catch (_) { return []; }
  }

  let index = 0;
  for (const group of groups) {
    const body = lab.querySelector(`[data-ml-group="${group.id}"]`);
    if (!body) continue;
    body.dataset.mlStorage = String(group.storage);
    const saved = savedIds(group.storage);
    const head = document.createElement("tr");
    head.className = "ml-group-row";
    head.innerHTML = `<th colspan="4" scope="colgroup"><span>${group.label} · in rotation</span><small>${group.studies.length} designs</small></th>`;
    body.append(head);
    for (const study of group.studies) {
      index += 1;
      const row = document.createElement("tr");
      // All artwork and metadata are authored locally, never supplied by a model.
      row.innerHTML = `<td><div class="ml-concept"><input type="checkbox" id="keep-${study.id}" data-study-id="${study.id}" aria-label="Keep ${study.name}"><div><label for="keep-${study.id}"><small>${String(index).padStart(2, "0")}</small>${study.name}</label><p>${study.note}</p></div></div></td><td><div class="ml-swatch">${study.html}</div></td><td><div class="ml-swatch ml-swatch-mono">${study.html}</div></td><td><div class="ml-context">${study.html}<span>Thinking<small>16px</small></span></div><div class="ml-context ml-context-20">${study.html}<span>Working<small>20px</small></span></div></td>`;
      row.querySelector("input").checked = saved.includes(study.id);
      body.append(row);
    }
  }

  function syncSelection(persist = false) {
    const filtered = filter.getAttribute("aria-pressed") === "true";
    const picked = [];
    let totalChecked = 0;
    for (const body of lab.querySelectorAll("[data-ml-group]")) {
      let groupChecked = 0;
      for (const row of body.querySelectorAll("tr:not(.ml-group-row)")) {
        const input = row.querySelector("input");
        if (input.checked) {
          groupChecked += 1;
          const name = input.getAttribute("aria-label").replace(/^Keep /, "");
          picked.push(`${name} (${input.dataset.studyId}) · ${body.dataset.mlTitle || body.dataset.mlGroup}`);
        }
        row.classList.toggle("is-selected", input.checked);
        row.hidden = filtered && !input.checked;
      }
      totalChecked += groupChecked;
      const head = body.querySelector(".ml-group-row");
      if (head) {
        head.hidden = filtered && groupChecked === 0;
        head.querySelector("small").textContent = groupChecked
          ? `${groupChecked} kept`
          : `${body.querySelectorAll("tr:not(.ml-group-row)").length} designs`;
      }
    }
    selection.value = picked.join("\n");
    lab.querySelector("[data-ml-count]").textContent = totalChecked;
    lab.querySelector("[data-ml-empty]").hidden = !filtered || totalChecked > 0;
    copy.disabled = clear.disabled = totalChecked === 0;
    if (persist) {
      try {
        for (const body of lab.querySelectorAll("[data-ml-group]")) {
          const ids = [...body.querySelectorAll("input[data-study-id]")]
            .filter(input => input.checked)
            .map(input => input.dataset.studyId);
          localStorage.setItem(`fuse-motion-shortlist-v${body.dataset.mlStorage}`, JSON.stringify(ids));
        }
      } catch (_) { feedback.textContent = "Browser storage is unavailable. Copy your shortlist before leaving."; }
    }
  }

  function syncMotion() {
    lab.classList.toggle("is-paused", paused);
    lab.classList.toggle("motion-enabled", !paused);
    pause.setAttribute("aria-pressed", String(paused));
    pause.textContent = paused ? "Play motion" : "Pause motion";
  }
  pause.addEventListener("click", () => {
    paused = !paused;
    motionChosen = true;
    syncMotion();
  });
  motionQuery.addEventListener("change", () => {
    if (!motionChosen) { paused = motionQuery.matches; syncMotion(); }
  });
  surface.addEventListener("click", () => {
    const light = lab.classList.toggle("is-light");
    surface.setAttribute("aria-pressed", String(light));
    surface.textContent = light ? "Dark surface" : "Light surface";
  });
  lab.querySelector(".ml-table").addEventListener("change", () => { feedback.textContent = ""; syncSelection(true); });
  filter.addEventListener("click", () => {
    filter.setAttribute("aria-pressed", String(filter.getAttribute("aria-pressed") !== "true"));
    syncSelection();
  });
  clear.addEventListener("click", () => {
    lab.querySelectorAll("input[data-study-id]").forEach(input => { input.checked = false; });
    feedback.textContent = "Selection cleared.";
    syncSelection(true);
  });
  copy.addEventListener("click", async () => {
    if (!selection.value) return;
    try {
      await navigator.clipboard.writeText(`Fuse motion shortlist (live rotation):\n${selection.value}`);
      feedback.textContent = "Shortlist copied. Send it back when you are ready.";
    } catch (_) {
      selection.focus();
      selection.select();
      feedback.textContent = "Clipboard unavailable. The shortlist is selected for manual copying.";
    }
  });
  syncSelection();
  syncMotion();
})();
