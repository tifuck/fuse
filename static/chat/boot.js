// Startup sequence: viewport pinning, mobile home, initial thread/artifact open.
import { S } from './state.js';

export function createBoot(ctx) {
  const { artifacts, composer, sidebar, sync } = ctx;
    if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", composer.pinVisualViewport);
    window.visualViewport.addEventListener("scroll", composer.pinVisualViewport);
  }

    window.addEventListener("resize", composer.pinVisualViewport);

    window.addEventListener("orientationchange", () => {
    composer.pinVisualViewport();
    window.setTimeout(composer.pinVisualViewport, 350);
  });

    if (S.historyMobile.matches && !new URLSearchParams(window.location.search).get("t")) {
    sidebar.setHistoryOpen(true, { home: true });
  }

    const initialNavigation = S.navigation;

    sync.connectSync(true)
    .then(() => {
      if (S.navigation !== initialNavigation) return;
      const params = new URLSearchParams(window.location.search);
      const wanted = (params.get("t") || "").trim();
      const art = (params.get("a") || "").trim();
      const open = wanted ? sidebar.openThread(wanted) : Promise.resolve();
      return open.then(() => {
        if (art && artifacts.artifactsInThread().some((t) => t.identifier === art)) return artifacts.showArtifact(art);
        artifacts.rememberLatestArtifact();
      });
    })
    .catch((error) => sync.showFailure("Chat history could not be loaded", error));

  return {

  };
}
