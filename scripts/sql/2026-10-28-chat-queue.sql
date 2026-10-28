-- Composer-queued messages move to the server (app/chat_queue.py) so they
-- send when the running turn ends even with no tab open. Additive.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that reads it):
--   mysql fuse_dev  < scripts/sql/2026-10-28-chat-queue.sql
--   mysql fuse_live < scripts/sql/2026-10-28-chat-queue.sql
CREATE TABLE IF NOT EXISTS chat_queue (
  thread_id BIGINT(20) NOT NULL,
  id VARCHAR(100) NOT NULL,
  user_id BIGINT(20) NOT NULL,
  body LONGTEXT NOT NULL,
  priority BIGINT(20) NOT NULL DEFAULT 0,
  created BIGINT(20) NOT NULL DEFAULT 0,
  held TINYINT(1) NOT NULL DEFAULT 0,
  updated_at VARCHAR(32) NOT NULL,
  PRIMARY KEY (thread_id, id),
  KEY chat_queue_held (held, thread_id),
  CONSTRAINT fk_chat_queue_0 FOREIGN KEY (thread_id) REFERENCES chat_threads (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin ROW_FORMAT=DYNAMIC;
