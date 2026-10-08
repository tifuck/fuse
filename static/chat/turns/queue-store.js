// Per-item records isolate tabs' enqueues; tombstones prevent stale resurrection.
const prefix = id => 'fuse.chat.queue.' + id + '.item.';
export function readQueue(id) {
  const base = 'fuse.chat.queue.' + id;
  const legacy = JSON.parse(localStorage.getItem(base) || '[]');
  if (Array.isArray(legacy)) for (const item of legacy) {
    if (item?.id && localStorage.getItem(prefix(id) + item.id) == null)
      localStorage.setItem(prefix(id) + item.id, JSON.stringify(item));
  }
  localStorage.removeItem(base);
  const items = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (!key?.startsWith(prefix(id))) continue;
    const item = JSON.parse(localStorage.getItem(key) || 'null');
    if (item && !item.deleted && typeof item.text === 'string' && !localStorage.getItem(prefix(id) + 'deleted.' + item.id)) items.push(item);
  }
  return items.sort((a, b) => (b.priority || 0) - (a.priority || 0) || (a.created || 0) - (b.created || 0) || a.id.localeCompare(b.id));
}
export function writeQueueItem(id, item) {
  const key = prefix(id) + item.id;
  if (localStorage.getItem(prefix(id) + 'deleted.' + item.id)) return;
  localStorage.setItem(key, JSON.stringify({ ...item, sending: false }));
}
export function deleteQueueItem(id, itemId) {
  localStorage.setItem(prefix(id) + 'deleted.' + itemId, 'true');
  localStorage.removeItem(prefix(id) + itemId);
  localStorage.removeItem('fuse.chat.queue.held.' + id + '.' + itemId);
}
