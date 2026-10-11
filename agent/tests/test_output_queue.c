#include "../src/agent.h"
#include "../src/outq.h"
#include <assert.h>
#include <pthread.h>
#include <string.h>

static void *terminal(void *unused) {
    (void)unused;
    send_res(NULL, "done", 1, 0, "", "", "");
    return NULL;
}

int main(void) {
    /* Every byte crosses as valid JSON: a raw control byte inside the string
     * makes the server's strict parser drop the whole frame. */
    char all[255];
    for (int i = 0; i < 255; i++) all[i] = (char)(i + 1);
    assert(send_chunk("job", "out", all, sizeof(all)) == 0);
    const unsigned char *frame = g_outq[g_out_head].data;
    for (size_t i = 0; i < g_outq[g_out_head].n; i++) assert(frame[i] >= 0x20);
    cJSON *parsed = cJSON_ParseWithLength((const char *)frame, g_outq[g_out_head].n);
    const char *data = json_cstr(parsed, "data", NULL);
    /* ASCII round-trips exactly; each lone high byte (not valid UTF-8, which
     * a WebSocket text frame must be) arrives as U+FFFD. */
    assert(data && strlen(data) == 127 + 128 * 3 && !memcmp(data, all, 127));
    for (int i = 0; i < 128; i++) assert(!memcmp(data + 127 + i * 3, "\xef\xbf\xbd", 3));
    cJSON_Delete(parsed);
    free(g_outq[g_out_head].data);
    g_out_head = (g_out_head + 1) % OUT_QMAX;
    g_out_n--;

    /* Streaming exec stops reading while the queue is within the reserve. */
    assert(out_has_room());
    for (int i = 0; i < OUT_QMAX - OUT_STREAM_RESERVE; i++)
        assert(send_chunk("job", "out", "x", 1) == 0);
    assert(!out_has_room());
    for (int i = OUT_QMAX - OUT_STREAM_RESERVE; i < OUT_QMAX; i++)
        assert(send_chunk("job", "out", "x", 1) == 0);
    assert(send_chunk("job", "out", "lost", 4) == -1);
    pthread_t producer;
    assert(pthread_create(&producer, NULL, terminal, NULL) == 0);
    pthread_mutex_lock(&g_out_mu);
    free(g_outq[g_out_head].data);
    g_out_head = (g_out_head + 1) % OUT_QMAX;
    g_out_n--;
    pthread_cond_signal(&g_out_space);
    pthread_mutex_unlock(&g_out_mu);
    pthread_join(producer, NULL);
    assert(g_out_n == OUT_QMAX);
    int last = (g_out_tail + OUT_QMAX - 1) % OUT_QMAX;
    assert(memmem(g_outq[last].data, g_outq[last].n, "\"t\":\"res\"", 9));
    for (int i = 0; i < OUT_QMAX; i++) free(g_outq[i].data);
    return 0;
}
