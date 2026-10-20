-- Store a run's provider transcript as provider_base + provider_tail behind a
-- virtual provider_messages, so a save that appends tool results writes only
-- the rows after the base (MariaDB rewrites a changed LONGTEXT whole).
-- Readers keep selecting provider_messages; SELECT * skips the invisible parts.
-- Both statements are INSTANT (metadata only). MariaDB refuses a virtual
-- column ADD combined with other changes, so between them the table has no
-- provider_messages: apply while no run is saving (Fuse quiet or stopped),
-- then restart Fuse so it detects the layout.
ALTER TABLE chat_runs
  CHANGE COLUMN provider_messages provider_base LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_nopad_bin DEFAULT NULL INVISIBLE,
  ADD COLUMN provider_tail LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_nopad_bin DEFAULT NULL INVISIBLE,
  ALGORITHM=INSTANT;
ALTER TABLE chat_runs
  ADD COLUMN provider_messages LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_nopad_bin GENERATED ALWAYS AS (
    CASE WHEN provider_tail IS NULL THEN provider_base
         WHEN provider_base IS NULL OR provider_base = '[]' THEN provider_tail
         WHEN provider_tail = '[]' THEN provider_base
         ELSE CONCAT(LEFT(provider_base, CHAR_LENGTH(provider_base) - 1), ',', SUBSTRING(provider_tail, 2)) END
  ) VIRTUAL,
  ALGORITHM=INSTANT;
