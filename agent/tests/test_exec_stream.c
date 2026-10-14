/* Streamed exec output survives a slow session: when the outbound queue is
 * full the worker stops reading (the child blocks) instead of dropping
 * chunks. Before this, ~1 MB of fast output lost everything past the queue. */
#include "../src/agent.h"
#include "../src/exec.h"
#include "../src/outq.h"
#include <assert.h>
#include <pthread.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>

static double cpu_s(void) {
    struct rusage r;
    getrusage(RUSAGE_SELF, &r);
    return r.ru_utime.tv_sec + r.ru_stime.tv_sec + (r.ru_utime.tv_usec + r.ru_stime.tv_usec) / 1e6;
}

static double mono_s(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

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
    /* Large enough that 32 KB reads still overflow the queue (backpressure). */
    assert(run_exec("/tmp", "seq 1 2000000", 30000, out, sizeof(out), err, sizeof(err), &code, NULL,
                    &timed_out, 1, "job", &clipped, &out_len, &err_len) == 0);
    g_stop = 1;
    pthread_join(drain, NULL);
    assert(code == 0);
    assert(!timed_out);
    assert(!clipped);
    assert(g_saw_first);
    assert(g_bytes == 14888896); /* every byte of `seq 1 2000000` arrived */
    assert(g_frames > OUT_QMAX);

    /* A child killed by a signal reports 128+N and says so on stderr. */
    code = -1;
    assert(run_exec("/tmp", "echo partial >&2; kill -KILL $$", 30000, out, sizeof(out), err, sizeof(err),
                    &code, NULL, &timed_out, 0, NULL, &clipped, &out_len, &err_len) == 0);
    assert(code == 137);
    assert(!timed_out);
    assert(!strcmp(err, "partial\n[killed by signal 9]\n"));
    assert(err_len == strlen(err));

    /* A child that closes its own stdout/stderr must not spin the worker:
     * EOF pipes left in the poll set burned a whole core until exit. */
    double c0 = cpu_s();
    assert(run_exec("/tmp", "exec >/dev/null 2>&1; sleep 1", 30000, out, sizeof(out), err, sizeof(err),
                    &code, NULL, &timed_out, 0, NULL, &clipped, &out_len, &err_len) == 0);
    assert(code == 0);
    assert(cpu_s() - c0 < 0.2);

    /* A background grandchild holding the pipes must not delay the result
     * (two 100 ms poll ticks before). */
    double w0 = mono_s();
    assert(run_exec("/tmp", "echo hi; (sleep 2 &)", 30000, out, sizeof(out), err, sizeof(err),
                    &code, NULL, &timed_out, 0, NULL, &clipped, &out_len, &err_len) == 0);
    assert(code == 0);
    assert(!strcmp(out, "hi\n"));
    assert(mono_s() - w0 < 0.1);

    /* Commands never read the agent's stdin. */
    assert(run_exec("/tmp", "cat", 5000, out, sizeof(out), err, sizeof(err),
                    &code, NULL, &timed_out, 0, NULL, &clipped, &out_len, &err_len) == 0);
    assert(code == 0 && !timed_out && out_len == 0);
    return 0;
}
