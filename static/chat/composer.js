// composer feature: composes its submodules and exposes one interface.
import { createInput } from './composer/input.js';
import { createSlash } from './composer/slash.js';
import { createAttachments } from './composer/attachments.js';
import { createDrafts } from './composer/drafts.js';

export function createComposer(ctx) {
  const input = createInput({ ...ctx,

  });
  const slash = createSlash({ ...ctx,
    input,
  });
  const attachments = createAttachments({ ...ctx,
    input,
  });
  const drafts = createDrafts({ ...ctx,

  });
  return {
    ...input, ...slash, ...attachments, ...drafts,
    get dockSpaceLocked() { return input.dockSpaceLocked; },
  };
}
