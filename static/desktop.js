/* Offer the build for this device first; the list keeps every platform. */
(() => {
  const primary = document.querySelector("[data-dl-primary]");
  if (!primary) return;
  const ua = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || navigator.userAgent || "";
  const want = /win/i.test(ua) ? ["windows"] : /mac/i.test(ua) ? ["mac"] : /linux|x11/i.test(ua) ? ["linux", "deb"] : [];
  for (const key of want) {
    const row = document.querySelector(`[data-dl-build="${key}"][data-dl-url]`);
    if (!row) continue;
    primary.href = row.dataset.dlUrl;
    primary.textContent = "Download for " + row.dataset.dlLabel;
    const file = document.querySelector("[data-dl-primary-file]");
    if (file) file.textContent = row.dataset.dlFile;
    row.classList.add("is-yours");
    break;
  }
})();
