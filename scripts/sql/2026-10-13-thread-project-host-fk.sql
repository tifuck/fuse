-- chat_threads.project_id / host_id had no foreign key: only app code kept
-- them pointing at live rows, and a delete racing a move (or older code) left
-- dangling ids that a reused id could later rebind. Null the dangling ones,
-- then let MariaDB keep them consistent (a deleted project or host unbinds
-- its threads, like delete_host already does by hand).
-- chat_threads' long unique public_id is a hidden hash column, so MariaDB can
-- only rebuild the table (ALGORITHM=COPY): writes to chat_threads block for
-- the copy (seconds on fuse_dev; stop fuse first on fuse_live).
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step:
--   mysql fuse_dev  < scripts/sql/2026-10-13-thread-project-host-fk.sql
--   mysql fuse_live < scripts/sql/2026-10-13-thread-project-host-fk.sql
UPDATE chat_threads t LEFT JOIN user_projects p ON p.id = t.project_id
  SET t.project_id = NULL WHERE t.project_id IS NOT NULL AND p.id IS NULL;
UPDATE chat_threads t LEFT JOIN user_hosts h ON h.id = t.host_id
  SET t.host_id = NULL WHERE t.host_id IS NOT NULL AND h.id IS NULL;
ALTER TABLE chat_threads
  ADD CONSTRAINT fk_chat_threads_project FOREIGN KEY IF NOT EXISTS (project_id)
    REFERENCES user_projects (id) ON DELETE SET NULL,
  ADD CONSTRAINT fk_chat_threads_host FOREIGN KEY IF NOT EXISTS (host_id)
    REFERENCES user_hosts (id) ON DELETE SET NULL;
