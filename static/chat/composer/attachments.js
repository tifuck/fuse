// Attachments: pick, resize, render, drop.
import { S } from '../state.js';
import { escapeHtml } from '../core.js';
import { newSession } from '../state.js';

// Console prints an ended background command or monitor as Claude Code's
// notice: 'Background command "<description>" completed · 12s'.

function dropAttachment(list, key, position) {
  return list.filter((att, i) => {
    const id = String(att.id || "");
    return id ? id !== key : i !== position;
  });
}

function isComposerPrintable(event) {
  return event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
}

const TEXT_EXTS = /\.(txt|md|markdown|csv|json|xml|html|htm|css|js|ts|py|rb|go|rs|java|c|h|cpp|sh|yml|yaml|toml|ini|log)$/i;

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Could not read " + (file.name || "file")));
    reader.readAsDataURL(file);
  });
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Could not read image"));
    img.src = url;
  });
}

function isTextFile(file) {
  const mime = (file.type || "").toLowerCase();
  if (mime.startsWith("text/")) return true;
  if (["application/json", "application/xml", "text/xml", "text/markdown", "text/csv"].includes(mime)) return true;
  return TEXT_EXTS.test(file.name || "");
}

export function createAttachments(ctx) {
  const { context, input, net, picker, sidebar, sync, traces, ui } = ctx;
    function removeAttachment(index, key, position) {
    if (traces.runIsActive(S.activeRun) || S.sending) {
      picker.setHint("Wait for the reply to finish before changing attachments.", true);
      return;
    }
    if (!net.requireLive()) return;
    const msg = S.messages[index];
    if (!msg || msg.role !== "user") return;
    const atts = Array.isArray(msg.attachments) ? msg.attachments : [];
    if (!atts.length) return;
    const kept = dropAttachment(atts, key, position);
    if (kept.length === atts.length) return;
    const empties = !kept.length && !(msg.content || "").trim();
    sidebar.mutateTranscript(() => {
      const target = S.messages[index];
      if (!target || target.role !== "user") return;
      const rest = dropAttachment(Array.isArray(target.attachments) ? target.attachments : [], key, position);
      if (rest.length === (target.attachments || []).length) return;
      if (!rest.length && !(target.content || "").trim()) S.messages.splice(index, 1);
      else target.attachments = rest;
    }, empties ? "Removed the attachment and its empty message." : "Attachment removed. It will leave the model's context on the next turn.");
  }

    const ATTACH_CHARS = Number(S.config.limits && S.config.limits.attachment_chars) || 1500000;

    function renderAttachList() {
    if (!S.attachList) return;
    if (!S.pendingAttachments.length) {
      S.attachList.hidden = true;
      S.attachList.innerHTML = "";
      input.syncComposer();
      context.syncContextMeter();
      input.syncEmptyComposer();
      return;
    }
    S.attachList.hidden = false;
    S.attachList.innerHTML = S.pendingAttachments
      .map((att, index) => {
        const thumb =
          att.kind === "image" && att.data
            ? `<button type="button" class="chat-attach-thumb" data-image-preview="${escapeHtml(att.data)}" data-image-name="${escapeHtml(att.name || "")}" aria-label="Preview ${escapeHtml(att.name || "image")}"><img src="${escapeHtml(att.data)}" alt=""></button>`
            : `<span class="chat-attach-icon">${ui.FILE_CHIP_ICON}</span>`;
        return `<div class="chat-attach-chip">${thumb}<span>${escapeHtml(att.name || "File")}</span><button type="button" data-remove-attach="${index}" aria-label="Remove ${escapeHtml(att.name || "file")}">×</button></div>`;
      })
      .join("");
    input.syncComposer();
    context.syncContextMeter();
    input.syncEmptyComposer();
  }

    async function resizeImage(file) {
    const original = await readAsDataUrl(file);
    if (file.type === "image/gif" && original.length <= ATTACH_CHARS) return { url: original, mime: "image/gif" };
    const img = await loadImage(original);
    const maxEdge = 1280;
    // PNG/WebP keep transparency and sharp text; re-encode only when too big.
    const keep = file.type === "image/png" || file.type === "image/webp";
    if (keep && original.length <= ATTACH_CHARS && Math.max(img.width || 0, img.height || 0) <= maxEdge) {
      return { url: original, mime: file.type };
    }
    const scale = Math.min(1, maxEdge / Math.max(img.width || 1, img.height || 1));
    const width = Math.max(1, Math.round((img.width || 1) * scale));
    const height = Math.max(1, Math.round((img.height || 1) * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Could not process image");
    // JPEG has no alpha: without a fill, transparent pixels turn black.
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(img, 0, 0, width, height);
    let quality = 0.84;
    let url = canvas.toDataURL("image/jpeg", quality);
    while (url.length > ATTACH_CHARS && quality > 0.5) {
      quality -= 0.08;
      url = canvas.toDataURL("image/jpeg", quality);
    }
    if (url.length > ATTACH_CHARS) throw new Error((file.name || "Image") + " is too large after shrinking");
    return { url, mime: "image/jpeg" };
  }

    async function fileToAttachment(file) {
    const mime = (file.type || "").toLowerCase();
    const name = (file.name || "attachment").slice(0, 200);
    if (mime.startsWith("image/") || /\.(png|jpe?g|gif|webp)$/i.test(name)) {
      const data = await resizeImage(file);
      return { id: newSession(), name, mime: data.mime, kind: "image", data: data.url };
    }
    if (mime === "application/pdf" || /\.pdf$/i.test(name)) {
      const url = await readAsDataUrl(file);
      if (url.length > ATTACH_CHARS) throw new Error((name || "PDF") + " is too large (about 1 MB max)");
      return { id: newSession(), name, mime: "application/pdf", kind: "file", data: url };
    }
    if (isTextFile(file)) {
      const text = await file.text();
      if (text.length > 200000) throw new Error((name || "File") + " is too long");
      return { id: newSession(), name, mime: mime || "text/plain", kind: "text", data: text };
    }
    throw new Error("Attach an image, PDF, or text file.");
  }

    async function addFiles(fileList) {
    const files = [...(fileList || [])];
    if (!files.length) return;
    // Reading a file is async: the chat may change, and a second paste may
    // run concurrently. Resolve the target list and the limit after the await.
    const origin = S.currentId;
    const target = () => {
      if (origin === S.currentId) return S.pendingAttachments;
      if (!S.draftFiles.has(origin)) S.draftFiles.set(origin, []);
      return S.draftFiles.get(origin);
    };
    const full = () => {
      if (target().length < S.ATTACH_MAX) return false;
      picker.setHint("Up to " + S.ATTACH_MAX + " attachments per message.", true);
      return true;
    };
    for (const file of files) {
      if (full()) break;
      try {
        const att = await fileToAttachment(file);
        if (full()) break;
        target().push(att);
      } catch (error) {
        sync.showFailure("Could not attach file", error);
      }
    }
    renderAttachList();
    context.warnTextOnlyModel();
  }

  return {
    addFiles,
    resizeImage,
    removeAttachment,
    renderAttachList,
    isComposerPrintable,
  };
}

export { isComposerPrintable };
