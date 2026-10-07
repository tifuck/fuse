// Loads the modularized chat runtime (static/chat/**) for the node suites.
// Tests exercise the real module exports and factories; cross-feature
// collaborators that a test does not cover are stubbed explicitly, exactly
// where the pre-modularization suites stubbed them in vm contexts.
//
// `source` keeps the legacy chat.js + theme concatenation for structural
// source pins (chat_appearance); behavioral tests use the modules below.
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '../static');
const theme = path.join(root, 'themes/console');

const Core = require(path.join(root, 'chat/core.js'));
const Semantics = require(path.join(root, 'chat/traces/semantics.js'));
const { createMerge } = require(path.join(root, 'chat/traces/merge.js'));
const Threads = require(path.join(root, 'chat/sidebar/threads.js'));
// perm-card copy helpers are pure exports on the queue submodule
const Perms = require(path.join(root, 'chat/perms/queue.js'));
const { createView } = require(path.join(root, 'chat/agents/view.js'));
const { createStatus } = require(path.join(root, 'chat/sync/status.js'));
const { S } = require(path.join(root, 'chat/state.js'));

// Real state owner: tests reset the fields their functions read.
function resetState(overrides = {}) {
  Object.assign(S, {
    activeRun: null, agentRun: null, agentsOpen: false,
    messages: [], log: { children: [] },
    completedJobs: new Map(), jobsRows: [], agentPendingEvents: [],
    agentViewId: '', stoppingRunId: '',
  }, overrides);
  return S;
}

// traces feature with stub collaborators (scheduleAgentView, normalizeTodos,
// startLiveClock were the stubbed seams in the vm-based suites too).
// theme-collaborator stubs matching the seams the vm suites stubbed
const UI_STUBS = {
  syncLiveDock() {}, workClocksLive() { return false; }, setLiveLabelText() {},
  setLivePhase() {}, currentActivity: () => null,
};
// Build without touching state; resetState is applied exactly once by the
// entry helpers below so caller overrides are never silently wiped.
// Stub collaborators match the seams the vm-based suites stubbed.
function buildTraces({ agents, transcript, ui } = {}) {
  const merge = createMerge({
    agents: agents || { scheduleAgentView() {} },
    transcript: transcript || { normalizeTodos: (t) => (t && t.todos) || [], startLiveClock() {} },
    ui: ui || UI_STUBS,
  });
  return { ...Semantics, ...merge };
}
function makeTraces({ state, ...rest } = {}) {
  resetState(state);
  return buildTraces(rest);
}

function makeAgentView({ state, traces, transcript, live, jobs, sync, context, net, ui } = {}) {
  resetState(state);
  return createView({
    chatApi: {},
    context: context || {},
    transcript: transcript || {},
    live: live || {},
    jobs: jobs || { refreshJobs() {} },
    ui: ui || UI_STUBS,
    traces: traces || buildTraces({ ui }),
    net: net || {},
    sync: sync || { applyProgressPayload() {} },
  });
}

function makeSyncStatus({ state, traces, transcript, sidebar, turns, ui } = {}) {
  resetState(state);
  return createStatus({
    S,
    picker: {},
    net: {},
    composer: {},
    sidebar: sidebar || {},
    transcript: transcript || {},
    turns: turns || {},
    chatApi: {},
    apply: {},
    ui: ui || UI_STUBS,
    traces: traces || buildTraces({ ui }),
  });
}

module.exports = {
  S, Core, Semantics, Threads, Perms, buildTraces, makeTraces, makeAgentView, makeSyncStatus,
  source: fs.readFileSync(path.join(root, 'chat.js'), 'utf8') + '\n' +
    fs.readdirSync(theme).filter(name => name.endsWith('.js') && name !== 'index.js')
      .sort().map(name => fs.readFileSync(path.join(theme, name), 'utf8')
        .replace(/^(function|const) /gm, '  $1 ')).join('\n'),
};
