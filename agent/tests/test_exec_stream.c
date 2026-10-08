/* Streamed exec output survives a slow session: when the outbound queue is
 * full the worker stops reading (the child blocks) instead of dropping
 * chunks. Before this, ~1 MB of fast output lost everything past the queue. */
#include "../src/agent.h"
#include "../src/exec.h"
#include "../src/outq.h"
#include <assert.h>
#include <pthread.h>
#include <string.h>
#include <time.h>

static volatile int g_stop = 0;
static int g_saw_first = 0;
static size_t g_frames = 0, g_bytes = 0;

static void *session(void *unused) {
    (void)unused;
    struct timespec pause = {0, 200000}; /* 0.2 ms per frame: slower than seq */
    for (;;) {
        pthread_mutex_lock(&g_out_mu);
        if (g_out_n == 0) {
            pthread_mutex_unlock(&g_out_mu);
            if (g_stop) return NULL;
            nanosleep(&pause, NULL);
            continue;
        }
        unsigned char *data = g_outq[g_out_head].data;
        size_t n = g_outq[g_out_head].n;
        g_outq[g_out_head].data = NULL;
        g_out_head = (g_out_head + 1) % OUT_QMAX;
        g_out_n--;
        pthread_cond_signal(&g_out_space);
        pthread_mutex_unlock(&g_out_mu);
        /* A raw newline on the wire is invalid JSON: the server drops it. */
        assert(!memchr(data, '\n', n));
        cJSON *frame = cJSON_ParseWithLength((const char *)data, n);
        const char *text = json_cstr(frame, "data", NULL);
        assert(text);
        if (!strncmp(text, "1\n2\n", 4)) g_saw_first = 1;
        g_bytes += strlen(text);
        cJSON_Delete(frame);
        g_frames++;
        free(data);
        nanosleep(&pause, NULL);
    }
}

int main(void) {
    pthread_t drain;
    assert(pthread_create(&drain, NULL, session, NULL) == 0);
    char out[EXEC_CAP], err[EXEC_CAP]; /* as jobs.c passes them */
    int code = -1, timed_out = 0, clipped = 0;
    size_t out_len = 0, err_len = 0;
    assert(run_exec("/tmp", "seq 1 200000", 30000, out, sizeof(out), err, sizeof(err), &code, NULL,
                    &timed_out, 1, "job", &clipped, &out_len, &err_len) == 0);
    g_stop = 1;
    pthread_join(drain, NULL);
    assert(code == 0);
    assert(!timed_out);
    assert(!clipped);
    assert(g_saw_first);
    assert(g_bytes == 1288895); /* every byte of `seq 1 200000` arrived */
    assert(g_frames > OUT_QMAX);
    return 0;
}
