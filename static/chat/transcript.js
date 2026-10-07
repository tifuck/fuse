// transcript feature: composes its submodules and exposes one interface.
import { createFollow } from './transcript/follow.js';
import { createTodos } from './transcript/todos.js';
import { createPaint } from './transcript/paint.js';
import { createClocks } from './transcript/clocks.js';
import { createReveal } from './transcript/reveal.js';

export function createTranscript(ctx) {
  const follow = createFollow({ ...ctx,

  });
  const todos = createTodos({ ...ctx,
    follow,
  });
  const paint = createPaint({ ...ctx,
    follow,
  });
  const clocks = createClocks({ ...ctx,
    paint,
  });
  const reveal = createReveal({ ...ctx,
    paint,
    follow,
  });
  return {
    ...follow, ...todos, ...paint, ...clocks, ...reveal,
    get resumeFollowOnScroll() { return follow.resumeFollowOnScroll; },
    set resumeFollowOnScroll(value) { follow.resumeFollowOnScroll = value; },
    get threadFollowFrame() { return follow.threadFollowFrame; },
    get pendingToolPaint() { return reveal.pendingToolPaint; },
    set pendingToolPaint(value) { reveal.pendingToolPaint = value; },
  };
}
