/* Identity-preserving reconciliation shared by every chat presentation. */
(() => {
  "use strict";
  const identity = node => {
    if (node.nodeType !== 1) return "";
    for (const name of ["data-message-id", "data-step", "data-content-key", "data-agent-id", "data-tool-section", "data-reason-preview", "data-activity-block", "data-content-block", "data-md-details"]) {
      if (node.hasAttribute(name)) return `${node.tagName}:${name}:${node.getAttribute(name)}`;
    }
    return "";
  };
  const compatible = (a, b) => a.nodeType === b.nodeType &&
    (a.nodeType !== 1 || a.tagName === b.tagName) && identity(a) === identity(b);

  function attributes(target, source) {
    const keepOpen = target.tagName === "DETAILS";
    for (const attr of [...target.attributes]) {
      if (keepOpen && attr.name === "open") continue;
      if (!source.hasAttribute(attr.name)) target.removeAttribute(attr.name);
    }
    for (const attr of source.attributes) {
      if (keepOpen && attr.name === "open") continue;
      if (target.getAttribute(attr.name) !== attr.value) target.setAttribute(attr.name, attr.value);
    }
  }

  function patch(target, source, options) {
    if (target.nodeType === 3 || target.nodeType === 8) {
      if (target.data !== source.data) {
        if (source.data.startsWith(target.data)) target.appendData(source.data.slice(target.data.length));
        else target.data = source.data;
      }
      return;
    }
    if (target.nodeType === 1) {
      // Islands own listeners, markdown, scroll controllers and animation
      // state. Their dedicated updater runs after structural reconciliation.
      if (options.island?.(target, source)) return;
      attributes(target, source);
    }
    children(target, source, options);
  }

  function children(target, source, options) {
    const old = [...target.childNodes];
    const keyed = new Map(old.map(node => [identity(node), node]).filter(([key]) => key));
    const used = new Set();
    let cursor = target.firstChild;
    for (const incoming of [...source.childNodes]) {
      const key = identity(incoming);
      let current = key ? keyed.get(key) : cursor;
      if (!current || used.has(current) || !compatible(current, incoming)) {
        current = incoming.cloneNode(true);
        target.insertBefore(current, cursor);
      } else {
        if (current !== cursor) target.insertBefore(current, cursor);
        patch(current, incoming, options);
      }
      used.add(current);
      cursor = current.nextSibling;
    }
    for (const obsolete of old) {
      if (!used.has(obsolete)) {
        options.dispose?.(obsolete);
        obsolete.remove();
      }
    }
  }

  function reconcile(target, html, options = {}) {
    const template = document.createElement("template");
    template.innerHTML = html;
    children(target, template.content, options);
  }
  window.FuseChatDOM = Object.freeze({ reconcile, patch, identity });
})();
