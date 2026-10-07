#ifndef FUSE_EDIT_H
#define FUSE_EDIT_H

/* Write-side filesystem ops: atomic/whole-file writes and exact edits. */

#include <stddef.h>

#include "agent.h"

int write_file_atomic(const char *path, const char *content);
int write_file_atomic_limit(const char *path, const char *content, long max);
int load_whole(const char *path, char **buf, size_t *len);
char *crlf_expand(const char *s, size_t n);
int apply_edit(char **bufp, size_t *lenp, const char *old, const char *newv, int all,
               long *reps_out, char *why, size_t whylen);

#endif
