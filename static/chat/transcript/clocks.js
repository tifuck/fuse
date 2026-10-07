// Live run clocks and spin state for status lines.
import { S } from '../state.js';

export function createClocks(ctx) {
  const { agents, paint, traces, ui } = ctx;
    // Work time, not wall time. The server's elapsedMs sums only the legs a run
  // actually executed (a stop, restart or resume never adds the pause), so
  // the clock anchors to it and ticks locally only while that run executes:
  // frozen while queued, re-anchored on every new leg and every checkpoint.
  function liveElapsedSeconds(msg, live) {
    const stored = Number(msg.elapsedMs);
    const known = msg.elapsedMs != null && Number.isFinite(stored) && stored >= 0;
    const run = traces.runForMessage(msg);
    if (!live || (run && run.status === "queued")) {
      delete msg._liveOrigin;
      if (!live) return known ? stored / 1000 : null;
      return known ? stored / 1000 : 0;
    }
    const leg = run ? `${run.id}:${run.resume_count || 0}` : "";
    if (!msg._liveOrigin || msg._elapsedObserved !== msg.elapsedMs || msg._liveLeg !== leg) {
      msg._elapsedObserved = msg.elapsedMs;
      msg._liveLeg = leg;
      if (known) msg._liveOrigin = Date.now() - stored;
      else {
        // First leg before its first checkpoint. Only a root turn's
        // created_at marks when this message began; a child row keeps its
        // original spawn time across resumes.
        const started = run && run === S.activeRun ? Date.parse(run.created_at || "") : NaN;
        msg._liveOrigin = Number.isFinite(started) ? Math.min(Date.now(), started) : Date.now();
      }
    }
    return Math.max(0, (Date.now() - msg._liveOrigin) / 1000);
  }

    // Claude Code's spinner verbs (2.1.287). It picks one when its spinner
  // starts; Fuse keeps one per turn so the word holds across repaints.


  // The spinner's word: one of Claude Code's verbs, fixed per turn. It only
  // says the run is alive; the todo list has its own section under it.


  // Claude Code's thinking status: "thinking", deepening at 10/20/30/45s,
  // then "thought for 4s" for 2s once the thought ends.


  // What the run is doing, in the spinner's parentheses. Claude Code names
  // only thinking there; Fuse also names the call that is running.

  // Claude Code's status row: "✻ Cogitating… (1m 3s · ↓ 1.2k tokens · thinking)".
  // Main run by default; the subagent dock passes its own meter and clock.

  function stopLiveClock() {
    if (S.liveClockTimer) {
      window.clearInterval(S.liveClockTimer);
      S.liveClockTimer = 0;
    }
  }

    // The open subagent pane keeps its own clocks; they tick only while it shows
  // a live child, and the timer stops once neither transcript needs it.
  function agentClockLive() {
    return Boolean(S.agentsOpen && S.agentViewId && S.agentRun && agents.agentLive(S.agentRun.status) && S.agentBodyEl);
  }

    function startLiveClock() {
    if (S.liveClockTimer) return;
    S.liveClockTimer = window.setInterval(() => {
      const child = agentClockLive();
      if (!S.streaming && !child && !ui.workClocksLive()) {
        stopLiveClock();
        return;
      }
      if (S.streaming) {
        const msg = paint.activeAssistant();
        const article = traces.articleFor(msg);
        if (msg && msg.role === "assistant" && article && article.classList.contains("is-assistant")) {
          ui.syncActivityElapsed(article, msg, true);
          ui.syncActivitySpin(article, msg, true);
          ui.syncConsoleClocks(article);
          ui.syncLiveDock();
        }
      }
      if ((child)) {
        ui.syncConsoleClocks(S.agentBodyEl);
        agents.syncAgentLiveDock();
      }
      if (!S.streaming) ui.syncWorkClocks();
    }, 250);
  }

  return {
    agentClockLive,
    liveElapsedSeconds,
    startLiveClock,
    stopLiveClock,
  };
}
