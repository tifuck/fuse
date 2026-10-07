#ifndef FUSE_OUTQ_H
#define FUSE_OUTQ_H

/* Outbound frame queue: worker threads enqueue, the session loop drains.
 * Reliable producers (terminal results) block for space; chunks drop. */

#include <pthread.h>

#include "agent.h"

struct tls;

struct out_frame {
    int opcode;
    unsigned char *data;
    size_t n;
};

extern struct out_frame g_outq[OUT_QMAX];
extern int g_out_head, g_out_tail, g_out_n;
extern pthread_mutex_t g_out_mu;
extern pthread_cond_t g_out_space;

int out_enqueue(int opcode, const unsigned char *data, size_t n);
int out_flush(struct tls *t);
int send_chunk(const char *id, const char *stream, const char *data, size_t n);
void send_res_tmo_len(struct tls *t, const char *id, int ok, int exit_code,
                      const char *out, size_t out_len, const char *err, size_t err_len,
                      const char *error, int timed_out, int clipped);
void send_res_tmo(struct tls *t, const char *id, int ok, int exit_code, const char *out,
                  const char *err, const char *error, int timed_out, int clipped);
void send_res(struct tls *t, const char *id, int ok, int exit_code, const char *out,
              const char *err, const char *error);

#endif
