#ifndef FUSE_UPDATE_H
#define FUSE_UPDATE_H

/* Self-update: manifest fetch, sha256 verify, flock ownership, staging. */

#include <stddef.h>

int ver_newer(const char *cand, const char *cur);
int update_is_self(const char *self, const char *expect_sha);
int update_lock_acquire(const char *self, char *lockpath, size_t cap);
int format_update_staging(char *dst, size_t cap, const char *self);
int maybe_update(int allow_exec);
/* Non-zero vetoes the post-update execv (work would be dropped). */
extern int (*update_exec_veto)(void);
int update_exec_pending(void);

#endif
