/* Workers start on demand: long commands holding every worker of the old
 * fixed pool (16) queued even a quick read behind them. */
#include "../src/agent.h"
#include "../src/jobs.h"
#include "../src/outq.h"
#include <assert.h>
#include <pthread.h>
#include <string.h>
#include <time.h>

static volatile int g_read_done = 0;

static void *session(void *unused) {
    (void)unused;
    struct timespec pause = {0, 1000000};
    while (!g_read_done) {
        pthread_mutex_lock(&g_out_mu);
        if (g_out_n == 0) {
            pthread_mutex_unlock(&g_out_mu);
            nanosleep(&pause, NULL);
            continue;
        }
        unsigned char *data = g_outq[g_out_head].data;
        size_t n = g_outq[g_out_head].n;
        g_outq[g_out_head].data = NULL;
        g_out_head = (g_out_head + 1) % OUT_QMAX;
        g_out_n--;
        pthread_cond_broadcast(&g_out_space);
        pthread_mutex_unlock(&g_out_mu);
        cJSON *frame = cJSON_ParseWithLength((const char *)data, n);
        if (!strcmp(json_cstr(frame, "id", ""), "r")) {
            assert(cJSON_IsTrue(cJSON_GetObjectItem(frame, "ok")));
            g_read_done = 1;
        }
        cJSON_Delete(frame);
        free(data);
    }
    return NULL;
}

int main(void) {
    pthread_t drain;
    assert(pthread_create(&drain, NULL, session, NULL) == 0);
    char buf[160];
    for (int i = 0; i < 24; i++) {
        snprintf(buf, sizeof(buf), "{\"t\":\"job\",\"id\":\"s%d\",\"op\":\"exec\",\"cmd\":\"sleep 2\",\"timeout_ms\":10000}", i);
        enqueue_job(NULL, cJSON_Parse(buf));
    }
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    enqueue_job(NULL, cJSON_Parse("{\"t\":\"job\",\"id\":\"r\",\"op\":\"read\",\"path\":\"/etc/hostname\"}"));
    pthread_join(drain, NULL);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double waited = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9;
    assert(waited < 1.0);
    printf("test_jobs_pool: OK (read answered in %.1f ms behind 24 running commands)\n", waited * 1000);
    return 0;
}
