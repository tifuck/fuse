/* Prism view: pointer-tracked light on tool rows and the length of the beam
   the streaming photon travels. Presentation only; inert in other views. */
(() => {
  const root = document.querySelector("[data-chat]");
  if (!root) return;
  const reduce = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
  let frame = 0;
  let pending = null;

  function active() {
    return root.dataset.view === "prism";
  }

  function paint() {
    frame = 0;
    if (!pending) return;
    const { head, x, y } = pending;
    pending = null;
    const box = head.getBoundingClientRect();
    head.style.setProperty("--mx", `${Math.round(x - box.left)}px`);
    head.style.setProperty("--my", `${Math.round(y - box.top)}px`);
  }

  root.addEventListener("pointermove", (event) => {
    if (!active() || reduce.matches || event.pointerType === "touch") return;
    const head = event.target.closest?.(".chat-step-head");
    if (!head) return;
    pending = { head, x: event.clientX, y: event.clientY };
    if (!frame) frame = requestAnimationFrame(paint);
  }, { passive: true });

  // The photon runs the full height of the live turn's beam.
  function measureBeam() {
    if (!active()) return;
    const beam = root.querySelector(".chat-turn:last-child .chat-activity-panel .chat-timeline");
    if (beam) beam.style.setProperty("--pz-beam", `${Math.max(64, beam.offsetHeight)}px`);
  }
  const log = root.querySelector("[data-chat-log]");
  if (log && typeof ResizeObserver === "function") {
    let queued = 0;
    const observer = new ResizeObserver(() => {
      if (queued) return;
      queued = requestAnimationFrame(() => { queued = 0; measureBeam(); });
    });
    observer.observe(log);
  }
  new MutationObserver(measureBeam).observe(root, { attributes: true, attributeFilter: ["data-view"] });
})();
