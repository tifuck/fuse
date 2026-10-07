// sync feature: composes its submodules and exposes one interface.
import { createStatus } from './sync/status.js';
import { createPersist } from './sync/persist.js';
import { createApply } from './sync/apply.js';
import { createStream } from './sync/stream.js';

export function createSync(ctx) {
  const status = createStatus({ ...ctx,
    apply: { reconcileThread: (...args) => apply.reconcileThread(...args) },
  });
  const persist = createPersist({ ...ctx,
    apply: { reconcileThread: (...args) => apply.reconcileThread(...args) },
    status,
  });
  const apply = createApply({ ...ctx,
    status,
    persist,
  });
  const stream = createStream({ ...ctx,
    apply,
    persist,
    status,
  });
  return { ...status, ...persist, ...apply, ...stream };
}
