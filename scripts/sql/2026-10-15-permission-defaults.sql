-- Default permission mode for new chats (ask / auto / bypass): one account
-- default for plain chats, one for project sessions, and an optional per-project
-- override ('' = use the account default). Also drops the retired bug-report
-- queue; dump it first if its rows are still wanted:
--   mariadb-dump fuse_live bug_reports > bug_reports-fuse_live.sql
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-10-15-permission-defaults.sql
--   mysql fuse_live < scripts/sql/2026-10-15-permission-defaults.sql
ALTER TABLE user_agent_settings
  ADD COLUMN IF NOT EXISTS default_chat_permissions VARCHAR(16) NOT NULL DEFAULT 'ask',
  ADD COLUMN IF NOT EXISTS default_project_permissions VARCHAR(16) NOT NULL DEFAULT 'ask';
ALTER TABLE user_projects
  ADD COLUMN IF NOT EXISTS default_permissions VARCHAR(16) NOT NULL DEFAULT '';
DROP TABLE IF EXISTS bug_reports;
