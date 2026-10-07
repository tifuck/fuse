// agents feature: composes its submodules and exposes one interface.
import { createLive } from './agents/live.js';
import { createView } from './agents/view.js';
import { createJobs } from './agents/jobs.js';

export function createAgents(ctx) {
  const live = createLive({ ...ctx,
    jobs: { renderJobsList: (...args) => jobs.renderJobsList(...args), tickJobTimes: (...args) => jobs.tickJobTimes(...args) },
    view: { pollAgentView: (...args) => view.pollAgentView(...args), refreshAgents: (...args) => view.refreshAgents(...args) },
  });
  const view = createView({ ...ctx,
    live,
    jobs: { refreshJobs: (...args) => jobs.refreshJobs(...args), renderJobsList: (...args) => jobs.renderJobsList(...args) },
  });
  const jobs = createJobs({ ...ctx,
    live,
  });
  return { ...live, ...view, ...jobs };
}
