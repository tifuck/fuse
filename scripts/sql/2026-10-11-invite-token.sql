-- Invite links: registration with a listed email must also present the
-- single-use token from the admin's invite link. Only its SHA-256 is stored.
-- Rows listed before this column have an empty hash and no longer admit a
-- registration; the admin removes and re-lists them to get a link.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-10-11-invite-token.sql
--   mysql fuse_live < scripts/sql/2026-10-11-invite-token.sql
ALTER TABLE registration_whitelist
  ADD COLUMN IF NOT EXISTS token_hash VARCHAR(64) CHARACTER SET ascii NOT NULL DEFAULT '';
