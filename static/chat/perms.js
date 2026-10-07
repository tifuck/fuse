// perms feature: composes its submodules and exposes one interface.
import { createQueue } from './perms/queue.js';
import { createMenu } from './perms/menu.js';

export function createPerms(ctx) {
  const queue = createQueue({ ...ctx,
    menu: { paintPermsMode: (...args) => menu.paintPermsMode(...args), refreshPerms: (...args) => menu.refreshPerms(...args) },
  });
  const menu = createMenu({ ...ctx,
    queue,
  });
  return { ...queue, ...menu };
}
