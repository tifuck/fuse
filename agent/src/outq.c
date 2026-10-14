/* Fuse connector: outbound frame queue and result/chunk envelopes. */
#include <pthread.h>
#include <time.h>

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

/* Takes ownership of copy (freed on failure). */
static int out_push(int opcode, unsigned char *copy, size_t n, int reliable) {
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
    unsigned char *copy = malloc(n ? n : 1);
    if (!copy) return -1;
    if (n) memcpy(copy, data, n);
    return out_push(opcode, copy, n, 0);
}

int out_flush(struct tls *t) {
    /* Peek, send, pop only on success: a frame whose send fails mid-stream
     * stays queued and is re-sent whole on the next connection. Partial
     * bytes went to a dead socket, so the re-send cannot duplicate.
     * Bounded per call so the session reads between batches. */
    for (int sent = 0; sent < OUT_FLUSH_BATCH; sent++) {
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
        pthread_cond_broadcast(&g_out_space); /* reliable producers and held streams */
        pthread_mutex_unlock(&g_out_mu);
        free(data);
    }
    return 0;
}

int out_has_room(void) {
    pthread_mutex_lock(&g_out_mu);
    int room = g_out_n < OUT_QMAX - OUT_STREAM_RESERVE;
    pthread_mutex_unlock(&g_out_mu);
    return room;
}

void out_wait_room(int ms) {
    struct timespec until;
    clock_gettime(CLOCK_REALTIME, &until);
    until.tv_sec += ms / 1000;
    until.tv_nsec += (long)(ms % 1000) * 1000000L;
    if (until.tv_nsec >= 1000000000L) {
        until.tv_sec++;
        until.tv_nsec -= 1000000000L;
    }
    pthread_mutex_lock(&g_out_mu);
    while (g_out_n >= OUT_QMAX - OUT_STREAM_RESERVE &&
           pthread_cond_timedwait(&g_out_space, &g_out_mu, &until) == 0) {
    }
    pthread_mutex_unlock(&g_out_mu);
}

/* Written straight into the queued buffer: the cJSON tree, its print and
 * the queue copy cost three passes over every output chunk. Output is
 * arbitrary bytes, so the escaper also covers NUL and invalid UTF-8. */
int send_chunk(const char *id, const char *stream, const char *data, size_t n) {
    if (!data || !n) return 0;
    if (!id) id = "";
    if (!stream) stream = "out";
    size_t idn = strlen(id), sn = strlen(stream);
    char *buf = malloc(64 + (idn + sn + n) * 6);
    if (!buf) return -1;
    char *w = stpcpy(buf, "{\"v\":1,\"t\":\"chunk\",\"id\":\"");
    w = json_escape_into(w, id, idn);
    w = stpcpy(w, "\",\"stream\":\"");
    w = json_escape_into(w, stream, sn);
    w = stpcpy(w, "\",\"data\":\"");
    w = json_escape_into(w, data, n);
    w = stpcpy(w, "\"}");
    return out_push(1, (unsigned char *)buf, (size_t)(w - buf), 0);
}

void send_res_tmo_len(struct tls *t, const char *id, int ok, int exit_code,
                      const char *out, size_t out_len, const char *err, size_t err_len,
                      const char *error, int timed_out, int clipped) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "v", 1);
    cJSON_AddStringToObject(o, "t", "res");
    cJSON_AddStringToObject(o, "id", id ? id : "");
    cJSON_AddBoolToObject(o, "ok", ok);
    cJSON_AddNumberToObject(o, "exit", exit_code);
    cJSON_AddItemToObject(o, "out", json_string_bytes(out ? out : "", out ? out_len : 0));
    cJSON_AddItemToObject(o, "err", json_string_bytes(err ? err : "", err ? err_len : 0));
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
         /* A refusal lost to a dead socket is queued (never waiting) for the
          * next session; dropped, the server would wait on a job that never ran. */
         if (!t) out_push(1, (unsigned char *)s, strlen(s), 1);
         else if (ws_send_text(t, s) != 0) out_push(1, (unsigned char *)s, strlen(s), 0);
         else free(s);
     }
}

void send_res_tmo(struct tls *t, const char *id, int ok, int exit_code, const char *out, const char *err, const char *error, int timed_out, int clipped) {
    send_res_tmo_len(t, id, ok, exit_code, out, out ? strlen(out) : 0,
                     err, err ? strlen(err) : 0, error, timed_out, clipped);
}

void send_res(struct tls *t, const char *id, int ok, int exit_code, const char *out, const char *err, const char *error) {
    send_res_tmo(t, id, ok, exit_code, out, err, error, 0, 0);
}
