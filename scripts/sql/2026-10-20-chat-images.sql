-- Large inline images in stored provider transcripts move to one row per
-- thread and image (app/chat_images.py); transcripts keep a fuse-image:<sha256>
-- reference. Additive: existing rows keep working inline.
-- Apply as root, fuse_dev first, then fuse_live as its own reviewed step
-- (before promoting the code that writes it):
--   mysql fuse_dev  < scripts/sql/2026-10-20-chat-images.sql
--   mysql fuse_live < scripts/sql/2026-10-20-chat-images.sql
CREATE TABLE IF NOT EXISTS chat_images (
  thread_id BIGINT(20) NOT NULL,
  sha CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  user_id BIGINT(20) NOT NULL,
  url LONGTEXT NOT NULL,
  created_at VARCHAR(32) NOT NULL,
  PRIMARY KEY (thread_id, sha),
  KEY chat_images_user_sha (user_id, sha),
  CONSTRAINT fk_chat_images_0 FOREIGN KEY (thread_id) REFERENCES chat_threads (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin ROW_FORMAT=DYNAMIC;
