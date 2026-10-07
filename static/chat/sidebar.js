// sidebar feature: composes its submodules and exposes one interface.
import { createDrawer } from './sidebar/drawer.js';
import { createUndo } from './sidebar/undo.js';
import { createThreads } from './sidebar/threads.js';
import { createProjects } from './sidebar/projects.js';
import { createSpinners } from './sidebar/spinners.js';
import { createRender } from './sidebar/render.js';

export function createSidebar(ctx) {
  const drawer = createDrawer({ ...ctx,

  });
  const undo = createUndo({ ...ctx,

  });
  const threads = createThreads({ ...ctx,
    spinners: { currentThreadBusy: (...args) => spinners.currentThreadBusy(...args) },
    render: { setHistoryTab: (...args) => render.setHistoryTab(...args), filteredHistory: (...args) => render.filteredHistory(...args), renderHistory: (...args) => render.renderHistory(...args), resetSearchLists: (...args) => render.resetSearchLists(...args), refreshThreads: (...args) => render.refreshThreads(...args) },
    projects: { sidebarProject: (...args) => projects.sidebarProject(...args), resolveProjectInfo: (...args) => projects.resolveProjectInfo(...args), projectIcon: (...args) => projects.projectIcon(...args), projectWorkspaceDetails: (...args) => projects.projectWorkspaceDetails(...args), editProject: (...args) => projects.editProject(...args), moveToProject: (...args) => projects.moveToProject(...args) },
    undo,
    drawer,
  });
  const projects = createProjects({ ...ctx,
    render: { historyIcons: (...args) => render.historyIcons(...args), runningFirst: (...args) => render.runningFirst(...args), refreshThreads: (...args) => render.refreshThreads(...args), renderHistory: (...args) => render.renderHistory(...args) },
    spinners: { ageChip: (...args) => spinners.ageChip(...args), runSpinMarkup: (...args) => spinners.runSpinMarkup(...args), threadIsRunning: (...args) => spinners.threadIsRunning(...args) },
    threads,
    drawer,
    undo,
  });
  const spinners = createSpinners({ ...ctx,
    render: { renderHistory: (...args) => render.renderHistory(...args) },
    threads,
  });
  const render = createRender({ ...ctx,
    projects,
    spinners,
    threads,
  });
  return { ...drawer, ...undo, ...threads, ...projects, ...spinners, ...render };
}
