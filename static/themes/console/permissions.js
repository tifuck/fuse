// permissions presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function permissionCardHtml(perm, msg) {
    if (!perm) return "";
    if (perm.kind === "tool_permission") return "";
    const status = perm.status || "pending";
    const pending = status === "pending";
    const files = (perm.files || []).map((name) => deps.escapeHtml(name)).join(" · ");
    const replace = perm.overwrite
      ? `<label class="hosts-field"><span><input type="checkbox" data-perm-replace> Replace existing \`${deps.escapeHtml(perm.slug || "")}\`${perm.origin_existing ? ` (${deps.escapeHtml(perm.origin_existing)})` : ""}</span></label>`
      : "";
    const actions = pending
      ? `<div class="chat-perm-actions"><button type="button" class="btn sm ghost" data-perm-reject>Discard</button><button type="button" class="btn sm" data-perm-save>Save skill</button></div>`
      : "";
    const fields = pending
      ? `<label class="hosts-field"><span>slug</span><input data-perm-slug maxlength="64" value="${deps.escapeHtml(perm.slug || "")}"></label>
         <label class="hosts-field"><span>when</span><input data-perm-desc maxlength="1024" value="${deps.escapeHtml(perm.description || "")}"></label>
         <label class="hosts-field"><span>body</span><textarea class="mono" data-perm-body rows="8"></textarea></label>
         <p class="usage-sub">${files || "SKILL.md"}</p>
         <label class="hosts-field"><span><input type="checkbox" data-perm-enable ${perm.enable === false ? "" : "checked"}> Enable after save</span></label>
         ${replace}`
      : `<p class="chat-perm-slug mono">${deps.escapeHtml(perm.slug || "")}</p><p class="usage-sub">${deps.escapeHtml(perm.description || "")}</p>`;
    return `<div class="chat-perm" data-perm-id="${deps.escapeHtml(perm.id)}" data-msg="${deps.escapeHtml(msg.id || "")}">
      <div class="chat-perm-head"><span>Skill proposal</span><span class="chat-perm-status">${deps.escapeHtml(status)}</span></div>
      ${fields}${actions}
    </div>`;
  }

function permQueueCardHtml(rec) {
    if (rec.status !== "pending") return "";
    const copy = deps.permCardCopy(rec);
    const who = rec.requester && rec.requester !== "Main agent"
      ? `<span class="chat-perm-card-agent is-sub">Subagent</span><span class="chat-perm-card-who">${deps.escapeHtml(rec.requester)}</span>`
      : `<span class="chat-perm-card-agent">Main</span>`;
    const bits = [copy.action, copy.host, copy.why].filter(Boolean);
    const meta = bits.length ? `<span class="chat-perm-card-meta">${deps.escapeHtml(bits.join(" · "))}</span>` : "";
    const grant = copy.path
      ? `<p class="chat-perm-card-grant"><span class="chat-perm-card-kind">${deps.escapeHtml(copy.noun)}</span><span class="mono chat-perm-card-path" title="${deps.escapeHtml(copy.path)}">${deps.escapeHtml(copy.path)}</span></p>`
      : "";
    const desc = copy.description ? `<p class="chat-perm-card-desc">${deps.escapeHtml(copy.description)}</p>` : "";
    const body = copy.command
      ? `${grant}${desc}<pre class="chat-perm-card-cmd mono">${deps.escapeHtml(copy.command)}</pre>`
      : `${desc}${grant || `<p class="chat-perm-card-detail mono">${deps.escapeHtml(copy.path || "this location")}</p>`}`;
    const expires = rec.expires_at || rec.expiresAt || "";
    const left = deps.permLeftS(expires);
    const frac = left == null ? 1 : Math.max(0, Math.min(1, left / deps.PERM_TOTAL_S));
    // The server clears grant_kind on shell asks no grant could ever cover.
    const shell = rec.tool === "run_terminal_command" || rec.tool === "monitor";
    const always = shell && !rec.grant_kind ? "" : `
            <button type="button" class="chat-perm-act is-split" data-perm-always aria-expanded="false" aria-label="Allow always options">
              <svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path d="M3.2 10.2 8 5.4l4.8 4.8" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
            <div class="chat-pop chat-perm-always-menu" data-perm-always-menu hidden>
              <button type="button" data-perm-decide="allow_session">Allow this ${deps.escapeHtml(copy.noun)} in this chat</button>
              <button type="button" data-perm-decide="allow_host">Always allow this ${deps.escapeHtml(copy.noun)}</button>
            </div>`;
    return `<li class="chat-perm-card is-pending${left != null && left <= 15 ? " is-low" : ""}" data-perm-card="${deps.escapeHtml(rec.id)}" data-perm-sig="${deps.escapeHtml(deps.permCardSignature(rec))}" role="alert" style="--perm-left:${frac}">
      <div class="chat-perm-card-row">
        <span class="chat-perm-card-dot" aria-hidden="true"></span>
        ${who}${meta}
        <span class="chat-perm-card-count mono" data-perm-count>${deps.escapeHtml(deps.fmtPermCountdown(expires))}</span>
        <div class="chat-perm-card-actions">
          <button type="button" class="chat-perm-act" data-perm-decide="deny">Deny</button>
          <span class="chat-perm-always">
            <button type="button" class="chat-perm-act is-allow" data-perm-decide="allow">Allow</button>${always}
          </span>
        </div>
      </div>
      ${body}
      <span class="chat-perm-card-bar" data-perm-bar aria-hidden="true"></span>
    </li>`;
  }

  return { permissionCardHtml, permQueueCardHtml };
}
