#ifndef FUSE_EXEC_H
#define FUSE_EXEC_H

/* Child process execution: spawn, capped capture, timeout and cancel kills. */

#include <stddef.h>

#include "jobs.h"

int run_exec(const char *cwd, const char *cmd, int timeout_ms, char *out, size_t ocap,
             char *errb, size_t ecap, int *code, struct job *job, int *timed_out, int stream,
             const char *job_id, int *clipped, size_t *out_len, size_t *err_len);

#endif
