// Shared page bindings: the document root, the reduced-motion query, TAU.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

export const html = document.documentElement;
export const reduce = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
export const TAU = Math.PI * 2;
