// Model list, effort mapping, selection.
import { S } from '../state.js';
import { defaultReasoningFor, resolveListedModel } from '../state.js';
import { escapeHtml, formatTokens } from '../core.js';

const EFFORT_LABELS = {
  none: "Off",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
};

const PICKER_CHECK =
  '<svg class="chat-picker-check" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3 8.6l3.2 3.2L13 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

const IMAGE_ICON =
  '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><rect x="2" y="3" width="12" height="10" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="6" cy="6.6" r="1.2" fill="currentColor"/><path d="M2.8 12l3.6-3.6 2.4 2.4 1.6-1.6 3 3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';

export function createList(ctx) {
  const { composer, context, net, sheet, sidebar } = ctx;
    function modelEfforts() {
    const model = context.currentModel();
    return (model && model.reasoning) || defaultReasoningFor(model && model.type);
  }

    function defaultEffort() {
    const model = context.currentModel();
    const efforts = modelEfforts();
    const wanted = (model && model.reasoningDefault) || "";
    if (wanted && efforts.includes(wanted)) return wanted;
    if (efforts.includes("medium")) return "medium";
    return efforts[0] || "medium";
  }

    function currentEffort() {
    const efforts = modelEfforts();
    const stored = S.effortByModel[S.modelId];
    if (stored && efforts.includes(stored)) return stored;
    return defaultEffort();
  }

    function effortLabel(effort) {
    const model = context.currentModel();
    if (model && model.type === "zai") return effort === "none" ? "Off" : "On";
    return EFFORT_LABELS[effort] || effort;
  }

    function setEffort(value) {
    const efforts = modelEfforts();
    if (!efforts.includes(value)) return;
    S.effortByModel[S.modelId] = value;
    try {
      localStorage.setItem(S.STORE_EFFORT, JSON.stringify(S.effortByModel));
    } catch (_) {}
    const model = context.currentModel();
    const label = effortLabel(value);
    const small = S.pickerHost.querySelector(".chat-picker-btn small");
    if (small) small.textContent = label;
    const btn = S.pickerHost.querySelector(".chat-picker-btn");
    if (btn && model) btn.title = model.id + " · " + label;
    syncEffortSlider(value);
  }

    function filteredModels() {
    const q = S.pickerFilter.trim().toLowerCase();
    const pinned = S.modelsById.get(S.pickerPinId);
    let list = S.allModels;
    if (pinned) {
      const same = S.allModels.filter((m) => m.type === pinned.type && m.id !== pinned.id);
      const rest = S.allModels.filter((m) => m.type !== pinned.type);
      list = [pinned, ...same, ...rest];
    }
    if (!q) return list;
    return list.filter(
      (model) =>
        model.name.toLowerCase().includes(q) ||
        model.id.toLowerCase().includes(q) ||
        model.title.toLowerCase().includes(q)
    );
  }

    function renderPickerList() {
    const menu = (S.pickerMenuEl || S.pickerHost).querySelector(".chat-picker-list");
    if (!menu) return;
    renderPickerThinking();
    const items = filteredModels();
    if (S.pickerIndex >= items.length) S.pickerIndex = Math.max(0, items.length - 1);
    if (!items.length) {
      menu.innerHTML = '<p class="chat-picker-empty">No models match.</p>';
      return;
    }
    const groups = [];
    items.forEach((model, index) => {
      let group = groups[groups.length - 1];
      if (!group || group.type !== model.type) {
        group = { type: model.type, title: model.title, items: [] };
        groups.push(group);
      }
      group.items.push({ model, index });
    });
    menu.innerHTML = groups
      .map(
        (group) =>
          // The provider mark sits once on the group label; rows carry the
          // name and context window only, so the list stays dense.
          `<div class="chat-picker-group"><p>${sidebar.providerLogo(group.type)}<span>${escapeHtml(group.title)}</span></p>${group.items
            .map(({ model, index }) => {
              const selected = model.id === S.modelId;
              const ctx = model.contextWindow ? formatTokens(model.contextWindow) : "";
              const row =
                `<button type="button" role="option" data-id="${escapeHtml(model.id)}" aria-selected="${
                  selected ? "true" : "false"
                }" class="${selected ? "is-selected" : ""}${index === S.pickerIndex ? " is-active" : ""}">` +
                `<span>${escapeHtml(model.name)}</span>` +
                (model.image ? `<i class="chat-picker-image" title="Reads images" aria-label="Reads images">${IMAGE_ICON}</i>` : "") +
                (ctx ? `<small class="chat-picker-meta" title="Context window">${ctx}</small>` : "") +
                `<i class="chat-picker-mark" aria-hidden="true">${selected ? PICKER_CHECK : ""}</i>` +
                `</button>`;
              return `<div class="chat-picker-row">${row}</div>`;
            })
            .join("")}</div>`
      )
      .join("");
  }

    function renderPickerThinking() {
    const host = (S.pickerMenuEl || S.pickerHost).querySelector(".chat-picker-thinking-host");
    if (!host) return;
    const model = context.currentModel();
    const efforts = modelEfforts();
    const effort = currentEffort();
    if (!efforts.length) {
      host.innerHTML = "";
      return;
    }
    const index = Math.max(0, efforts.indexOf(effort));
    const ticks = efforts
      .map(
        (_, i) =>
          `<i style="left:${
            efforts.length > 1 ? ((i / (efforts.length - 1)) * 100).toFixed(4) : 50
          }%"></i>`
      )
      .join("");
    host.innerHTML =
      // One row: label, slider, value.
      `<section class="chat-picker-thinking" aria-label="Thinking mode">` +
      `<p class="chat-picker-thinking-label">Thinking</p>` +
      `<div class="chat-picker-efforts">` +
      `<div class="chat-picker-efforts-ticks" aria-hidden="true">${ticks}</div>` +
      `<input type="range" class="chat-picker-efforts-slider" data-effort-slider min="0" max="${
        efforts.length - 1
      }" step="1" value="${index}" aria-label="Thinking mode" aria-valuetext="${escapeHtml(
        effortLabel(effort)
      )}">` +
      `</div><small class="chat-picker-thinking-value">${escapeHtml(effortLabel(effort))}</small></section>`;
    syncEffortSlider(effort);
  }

    function syncEffortSlider(effort) {
    const menu = S.pickerMenuEl || S.pickerHost;
    const slider = menu.querySelector("[data-effort-slider]");
    if (!slider) return;
    const efforts = modelEfforts();
    const index = Math.max(0, efforts.indexOf(effort));
    if (Number(slider.value) !== index) slider.value = index;
    const span = efforts.length > 1 ? index / (efforts.length - 1) : 1;
    slider.style.setProperty("--effort-fill", (span * 100).toFixed(2) + "%");
    slider.setAttribute("aria-valuenow", String(index));
    slider.setAttribute("aria-valuetext", effortLabel(efforts[index]));
    const value = menu.querySelector(".chat-picker-thinking-value");
    if (value) value.textContent = effortLabel(efforts[index]);
  }

    function selectModel(id, dismiss) {
    id = resolveListedModel(id) || id;
    if (!S.modelsById.has(id)) return;
    if (id !== S.modelId && !net.requireLive()) return;
    if (id !== S.modelId) context.warnModelCacheReset(id);
    S.modelId = id;
    S.pickerIndex = Math.max(0, filteredModels().findIndex((model) => model.id === id));
    composer.persist();
    if (dismiss || !S.pickerOpen) {
      sheet.closePicker();
      sheet.renderPickerButton();
      const btn = S.pickerHost.querySelector(".chat-picker-btn");
      if (btn) btn.focus();
    } else {
      S.pickerFilter = "";
      const model = context.currentModel();
      const effort = currentEffort();
      const btn = S.pickerHost.querySelector(".chat-picker-btn");
      const logo = btn && btn.querySelector(".provider-logo");
      if (logo) logo.outerHTML = sidebar.providerLogo(model.type);
      if (btn) {
        btn.querySelector("span").textContent = model.name;
        btn.querySelector("small").textContent = effortLabel(effort);
        btn.title = model.id + " · " + effortLabel(effort);
      }
      const search = S.pickerMenuEl && S.pickerMenuEl.querySelector(".chat-picker-search");
      if (search) search.value = "";
      renderPickerList();
      if (search && !S.pickerSheetQuery.matches) search.focus();
    }
    composer.syncComposer();
    context.warnTextOnlyModel();
    if (S.liveMeter) {
      const changed = S.liveMeter.model !== id;
      S.liveMeter = { ...S.liveMeter, model: id, limit: context.modelWindow() };
      if (changed) {
        S.liveMeter.estimated = true;
        S.liveMeter.provider_usage = null;
        delete S.liveMeter.calibration;
        if (S.liveMeter.raw_parts) {
          S.liveMeter.used = S.liveMeter.raw_parts.total;
          S.liveMeter.parts = S.liveMeter.raw_parts;
          S.liveMeter.basis = "payload_chars_v1";
          delete S.liveMeter.raw_parts;
        }
      }
    }
    S.meterAnchor = null;
    context.syncContextMeter();
  }

  return {
    currentEffort,
    effortLabel,
    filteredModels,
    modelEfforts,
    renderPickerList,
    selectModel,
    setEffort,
  };
}
