-- Host-wide grants store thread_id NULL, and a UNIQUE key treats every NULL
-- as distinct, so INSERT IGNORE never deduplicated them: approving the same
-- path twice stored two rows, and revoking one left the other granting
-- access. Drop the duplicates (keep the oldest), then key on a stored
-- COALESCE(thread_id, 0) instead.
DELETE g FROM chat_permission_grants g
  JOIN chat_permission_grants k
    ON k.thread_id <=> g.thread_id AND k.host_id = g.host_id
   AND k.kind = g.kind AND k.value = g.value AND k.id < g.id;
ALTER TABLE chat_permission_grants
  ADD COLUMN thread_key bigint(20) GENERATED ALWAYS AS (COALESCE(thread_id, 0)) STORED,
  DROP INDEX sqlite_autoindex_chat_permission_grants_1,
  ADD UNIQUE KEY chat_perm_grants_scope (thread_key, host_id, kind, value) USING HASH;
