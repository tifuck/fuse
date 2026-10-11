// Per-item records isolate tabs' enqueues; tombstones prevent stale resurrection.
const prefix = id => 'fuse.chat.queue.' + id + '.item.';
// A tombstone only has to outlive another tab's stale copy of the item; past
// this it is swept, or every send/cancel would leave a key behind forever.
const TOMBSTONE_MS = 24 * 60 * 60 * 1000;
export function readQueue(id) {
  const base = 'fuse.chat.queue.' + id;
  const legacy = JSON.parse(localStorage.getItem(base) || '[]');
  if (Array.isArray(legacy)) for (const item of legacy) {
    if (item?.id && localStorage.getItem(prefix(id) + item.id) == null)
      localStorage.setItem(prefix(id) + item.id, JSON.stringify(item));
  }
  localStorage.removeItem(base);
  const keys = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith(prefix(id))) keys.push(key);
  }
  const items = [];
  const now = Date.now();
  for (const key of keys) {
    if (key.startsWith(prefix(id) + 'deleted.')) {
      // Legacy tombstones hold "true" (no time): treat them as expired.
      const at = Number(localStorage.getItem(key));
      if (!Number.isFinite(at) || now - at > TOMBSTONE_MS) localStorage.removeItem(key);
      continue;
    }
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
  localStorage.setItem(prefix(id) + 'deleted.' + itemId, String(Date.now()));
  localStorage.removeItem(prefix(id) + itemId);
  localStorage.removeItem('fuse.chat.queue.held.' + id + '.' + itemId);
}
