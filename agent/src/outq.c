/* Fuse connector: outbound frame queue and result/chunk envelopes. */
#include <pthread.h>

#include "agent.h"
#include "lifecycle.h"
#include "net.h"
#include "outq.h"

struct out_frame g_outq[OUT_QMAX];

int g_out_head = 0, g_out_tail = 0, g_out_n = 0;

pthread_mutex_t g_out_mu = PTHREAD_MUTEX_INITIALIZER;

pthread_cond_t g_out_space = PTHREAD_COND_INITIALIZER;

static void out_wake(void) {
    /* Pure wakeup: byte 0 carries no wake reason. */
    if (g_wake_wr >= 0) {
        char x = 0;
        (void)!write(g_wake_wr, &x, 1);
    }
}

static int out_enqueue_mode(int opcode, const unsigned char *data, size_t n, int reliable) {
    unsigned char *copy = malloc(n ? n : 1);
    if (!copy) return -1;
    if (n) memcpy(copy, data, n);
    pthread_mutex_lock(&g_out_mu);
    while (reliable && g_out_n >= OUT_QMAX)
        pthread_cond_wait(&g_out_space, &g_out_mu);
    if (g_out_n >= OUT_QMAX) {
        pthread_mutex_unlock(&g_out_mu);
        free(copy);
        return -1;
    }
    g_outq[g_out_tail].opcode = opcode;
    g_outq[g_out_tail].data = copy;
    g_outq[g_out_tail].n = n;
    g_out_tail = (g_out_tail + 1) % OUT_QMAX;
    g_out_n++;
    pthread_mutex_unlock(&g_out_mu);
    out_wake();
    return 0;
}

int out_enqueue(int opcode, const unsigned char *data, size_t n) {
    return out_enqueue_mode(opcode, data, n, 0);
}

int out_flush(struct tls *t) {
    /* Peek, send, pop only on success: a frame whose send fails mid-stream
     * stays queued and is re-sent whole on the next connection. Partial
     * bytes went to a dead socket, so the re-send cannot duplicate. */
    for (;;) {
        pthread_mutex_lock(&g_out_mu);
        if (g_out_n == 0) {
            pthread_mutex_unlock(&g_out_mu);
            return 0;
        }
        int opcode = g_outq[g_out_head].opcode;
        unsigned char *data = g_outq[g_out_head].data;
        size_t n = g_outq[g_out_head].n;
        pthread_mutex_unlock(&g_out_mu);
        int rc = (t && t->ssl) ? ws_send(t, opcode, data, n) : -1;
        if (rc != 0) return -1;
        pthread_mutex_lock(&g_out_mu);
        g_outq[g_out_head].data = NULL;
        g_out_head = (g_out_head + 1) % OUT_QMAX;
        g_out_n--;
        pthread_cond_signal(&g_out_space);
        pthread_mutex_unlock(&g_out_mu);
        free(data);
    }
}

int send_chunk(const char *id, const char *stream, const char *data, size_t n) {
    if (!data || !n) return 0;
    char *tmp = malloc(n + 1);
    if (!tmp) return -1;
    memcpy(tmp, data, n);
    tmp[n] = 0;
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "v", 1);
    cJSON_AddStringToObject(o, "t", "chunk");
    cJSON_AddStringToObject(o, "id", id ? id : "");
    cJSON_AddStringToObject(o, "stream", stream ? stream : "out");
    cJSON_AddStringToObject(o, "data", tmp);
    free(tmp);
    char *s = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    int rc = -1;
    if (s) {
        rc = out_enqueue(1, (const unsigned char *)s, strlen(s));
        free(s);
    }
    return rc;
}

void send_res_tmo(struct tls *t, const char *id, int ok, int exit_code, const char *out, const char *err, const char *error, int timed_out, int clipped) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "v", 1);
    cJSON_AddStringToObject(o, "t", "res");
    cJSON_AddStringToObject(o, "id", id ? id : "");
    cJSON_AddBoolToObject(o, "ok", ok);
    cJSON_AddNumberToObject(o, "exit", exit_code);
    cJSON_AddStringToObject(o, "out", out ? out : "");
    cJSON_AddStringToObject(o, "err", err ? err : "");
    if (error && error[0]) cJSON_AddStringToObject(o, "error", error);
    if (timed_out) cJSON_AddBoolToObject(o, "timeout", 1);
    cJSON_AddBoolToObject(o, "termination_confirmed", exit_code >= 0);
    /* Reported from the operation that produced the output. Matching the
     * marker text instead would flag any file that mentions it. */
    cJSON_AddBoolToObject(o, "truncated", clipped);
     char *s = cJSON_PrintUnformatted(o);
     cJSON_Delete(o);
     if (s) {
         /* Only the network thread supplies t (intake rejection). It must
          * never wait for itself to drain the outbound queue. Workers retain
          * their slot until their terminal result has bounded queue space. */
         if (t) ws_send_text(t, s);
         else out_enqueue_mode(1, (const unsigned char *)s, strlen(s), 1);
         free(s);
     }
}

void send_res(struct tls *t, const char *id, int ok, int exit_code, const char *out, const char *err, const char *error) {
    send_res_tmo(t, id, ok, exit_code, out, err, error, 0, 0);
}
