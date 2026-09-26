-- Integer-width optimistic guard for chat_runs checkpoints.
-- The checkpoint writer previously sent the whole request blob back as
-- `COALESCE(request,'')=?` to detect a concurrent request change. This stored
-- digest changes exactly when request changes, so the guard compares 32 hex
-- characters. Hex text (not BINARY) keeps SELECT * rows JSON-serializable.
-- App code falls back to the blob guard while this column is absent.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step:
--   mysql fuse_dev  < scripts/sql/2026-09-25-chat-runs-request-digest.sql
--   mysql fuse_live < scripts/sql/2026-09-25-chat-runs-request-digest.sql
-- fuse_live rebuilds chat_runs (about 0.5 GB); restart fuse afterwards so the
-- running process detects the column.
ALTER TABLE chat_runs
  ADD COLUMN IF NOT EXISTS request_digest CHAR(32) CHARACTER SET ascii COLLATE ascii_bin
  GENERATED ALWAYS AS (MD5(request)) STORED;
