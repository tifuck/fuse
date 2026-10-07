// Tool row reveal gestures and motion batching.
import { S } from '../state.js';

let toolGestureTimer = 0;

let pendingToolPaint = null;

const motionPaints = new Map();

function revealToolRow(row) {
  if (!row) return;
  const activity = row.closest("[data-activity]");
  if (activity && !activity.classList.contains("is-open")) activity.querySelector("button[data-activity-summary]")?.click();
  const group = row.parentElement?.closest(".is-stack, .is-console-group");
  if (group) {
    const toggle = group.querySelector(":scope > .chat-step-main > .chat-step-head > [data-step-toggle]");
    if (toggle?.getAttribute("aria-expanded") !== "true") toggle?.click();
  }
  const toggle = row.querySelector(":scope > .chat-step-main > .chat-step-head > [data-step-toggle]");
  if (toggle?.getAttribute("aria-expanded") !== "true") toggle?.click();
}

export function createReveal(ctx) {
  const { agents, follow, paint, ui } = ctx;
    function flushToolGesture() {
    if (toolGestureTimer) clearTimeout(toolGestureTimer);
    toolGestureTimer = 0;
    S.toolGestureOwner = null;
    const pending = pendingToolPaint;
    const full = S.pendingFullPaint;
    const agent = S.pendingAgentPaint;
    const list = S.pendingAgentListPaint;
    pendingToolPaint = null;
    S.pendingFullPaint = S.pendingAgentPaint = S.pendingAgentListPaint = false;
    if (full) paint.paint();
    else if (pending?.article.isConnected) ui.updateAssistant(pending.article, pending.msg, pending.live);
    if (agent && S.agentsOpen) agents.renderAgentView();
    if (list && S.agentsOpen) agents.renderAgentsList();
  }

    function beginToolGesture(event) {
    const control = event.target.closest?.("[data-step-toggle], [data-think-size], [data-activity-summary], .chat-step-open, [data-open-agent], [data-agent-id]");
    if (!control) return;
    flushToolGesture();
    if (S.log.contains(control)) follow.pauseThreadFollow();
    S.toolGestureOwner = control.closest(".chat-turn") || S.agentsListEl;
  }

    function endToolGesture() {
    if (!S.toolGestureOwner) return;
    if (toolGestureTimer) clearTimeout(toolGestureTimer);
    toolGestureTimer = setTimeout(flushToolGesture, 0); // after the native click
  }

    // Collapsed tool rows render an empty details body carrying a token for
  // their trace (see toolStepHtml). Weak references keep the registry from
  // pinning traces of threads that are no longer shown.

  function setToolReveal(reveal, open, fromHeight) {
    if (!reveal) return;
    if (open) void 0;
    const owner = reveal.closest(".chat-turn");
    const start = fromHeight ?? reveal.getBoundingClientRect().height;
    reveal._disclosureAnimation?.cancel();
    reveal.classList.toggle("is-open", open);
    if (S.reduceMotion.matches) return;
    reveal.style.display = "block";
    reveal.style.overflow = "hidden";
    const end = open ? reveal.scrollHeight : 0;
    const animation = reveal.animate([{ height: `${start}px`, opacity: open ? 0.3 : 1 }, { height: `${end}px`, opacity: open ? 1 : 0 }],
      { duration: (S.MOTION.fast), easing: S.MOTION.snap });
    reveal._disclosureAnimation = animation;
    if (owner) S.toolMotions.set(owner, (S.toolMotions.get(owner) || 0) + 1);
    const finish = () => {
      if (reveal._disclosureAnimation === animation) {
        reveal.style.display = ""; reveal.style.overflow = "";
        reveal._disclosureAnimation = null;
      }
      if (owner) {
        const n = (S.toolMotions.get(owner) || 1) - 1;
        if (n) S.toolMotions.set(owner, n);
        else {
          S.toolMotions.delete(owner);
          const paint = motionPaints.get(owner);
          motionPaints.delete(owner);
          if (paint && owner.isConnected) ui.updateAssistant(owner, paint.msg, paint.live);
          if (S.pendingAgentPaint) { S.pendingAgentPaint = false; agents.renderAgentView(); }
        }
      }
    };
    animation.onfinish = finish;
    animation.oncancel = finish;
  }

  return {
    beginToolGesture,
    endToolGesture,
    flushToolGesture,
    setToolReveal,
    motionPaints,
    get pendingToolPaint() { return pendingToolPaint; },
    set pendingToolPaint(value) { pendingToolPaint = value; },
    revealToolRow,
  };
}

export { motionPaints, pendingToolPaint, revealToolRow };
