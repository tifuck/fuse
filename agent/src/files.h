#ifndef FUSE_FILES_H
#define FUSE_FILES_H

/* Read-side filesystem ops: path expansion, text/binary reads, listings. */

#include <stddef.h>

#include "agent.h"

#define BINARY_FILE_MAX (20 * 1024 * 1024)
/* 180 KiB: base64 plus the result envelope stays under the hub's
 * RESULT_CHARS (262144) clip. Keep in sync with binaryChunkMax in
 * agent/windows/fs.go and _BINARY_CHUNK in app/workspace.py. */
#define BINARY_CHUNK_MAX 184320

int expand_home(const char *path, char *out, size_t cap);
char *read_binary(const char *path, int offset, int limit);
int read_file_slice(const char *path, int offset, int limit, char *out, size_t cap);
char *browse_json(const char *path);
int list_dir(const char *path, char *out, size_t cap);

#endif
