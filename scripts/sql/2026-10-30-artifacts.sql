-- Artifacts parity with Claude Code (app/artifacts.py): a rotatable share
-- link separate from the owner's identifier, a pinned shared version, a tab
-- icon, Markdown versions with a change note, and a per-account on/off
-- switch. Additive. Existing public links keep working: their share token is
-- their identifier until the owner resets the link.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-10-30-artifacts.sql
--   mysql fuse_live < scripts/sql/2026-10-30-artifacts.sql
ALTER TABLE artifacts
  ADD COLUMN IF NOT EXISTS share_token VARCHAR(64) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS shared_version BIGINT(20) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS icon VARCHAR(32) NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS artifacts_share_token ON artifacts (share_token);
UPDATE artifacts SET share_token = public_id WHERE visibility = 'public' AND share_token IS NULL;
ALTER TABLE artifact_versions
  ADD COLUMN IF NOT EXISTS doc_format VARCHAR(16) NOT NULL DEFAULT 'html',
  ADD COLUMN IF NOT EXISTS note VARCHAR(400) NOT NULL DEFAULT '';
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS chat_artifacts BIGINT(20) NOT NULL DEFAULT 1;
