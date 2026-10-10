/* Models page: find an id across every provider, and expand long families. */
(() => {
  const root = document.querySelector("[data-models]");
  if (!root) return;
  const find = root.querySelector("[data-models-find]");
  const none = root.querySelector("[data-models-none]");

  function expand(family, on) {
    family.querySelectorAll("[data-models-extra]").forEach((li) => { li.hidden = !on; });
    const more = family.querySelector("[data-models-more]");
    if (more) more.hidden = on;
  }

  root.addEventListener("click", (event) => {
    const more = event.target.closest("[data-models-more]");
    if (more) expand(more.closest("[data-models-family]"), true);
  });

  find?.addEventListener("input", () => {
    const q = find.value.trim().toLowerCase();
    let shown = 0;
    root.querySelectorAll("[data-models-family]").forEach((family) => {
      let hits = 0;
      family.querySelectorAll(".catalog-ids li").forEach((li) => {
        const match = !q || li.textContent.toLowerCase().includes(q);
        li.hidden = q ? !match : li.hasAttribute("data-models-extra") && !family.dataset.expanded;
        if (match) hits += 1;
      });
      const more = family.querySelector("[data-models-more]");
      if (more) more.hidden = Boolean(q) || Boolean(family.dataset.expanded);
      family.hidden = Boolean(q) && !hits;
      if (q && hits) family.open = true;
      if (!family.hidden && family.closest("[data-tab-panel]:not(.hidden)")) shown += hits;
    });
    if (none) none.hidden = !q || shown > 0;
  });

  root.addEventListener("click", (event) => {
    const more = event.target.closest("[data-models-more]");
    if (more) more.closest("[data-models-family]").dataset.expanded = "1";
  });
})();
