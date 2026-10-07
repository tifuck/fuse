// traces feature: composes its submodules and exposes one interface.
import * as semantics from './traces/semantics.js';
import { createMerge } from './traces/merge.js';

export function createTraces(ctx) {
  const merge = createMerge({ ...ctx,

  });
  return { ...semantics, ...merge };
}
