// turns feature: composes its submodules and exposes one interface.
import { createQueue } from './turns/queue.js';
import { createSend } from './turns/send.js';

export function createTurns(ctx) {
  const queue = createQueue({ ...ctx,
    send: { completeTurn: (...args) => send.completeTurn(...args), stopActiveRun: (...args) => send.stopActiveRun(...args) },
  });
  const send = createSend({ ...ctx,
    queue,
  });
  return { ...queue, ...send };
}
