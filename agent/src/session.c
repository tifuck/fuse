/* Fuse connector: the connect/hello/session loop and one-shot pairing. */
#include <poll.h>
#include <sys/random.h>

#include <openssl/ssl.h>

#include "agent.h"
#include "config.h"
#include "jobs.h"
#include "lifecycle.h"
#include "net.h"
#include "outq.h"
#include "reconnect.h"
#include "session.h"
#include "update.h"

/* The live session socket, for the update exec veto below. */
static struct tls *s_live_tls = NULL;

/* update_exec_veto: jobs queued or running, or frames waiting unread on the
 * live socket (the server sent work while the update was downloading). An
 * execv now would drop that work. */
static int work_waiting(void) {
    if (!agent_idle()) return 1;
    struct tls *t = s_live_tls;
    if (!t) return 0;
    if (t->pb_off < t->pb_len || (t->ssl && SSL_pending(t->ssl) > 0)) return 1;
    struct pollfd p = {t->fd, POLLIN, 0};
    return poll(&p, 1, 0) > 0 && (p.revents & POLLIN);
}

/* Server-supplied strings go to stderr (journal): keep them one line and
 * free of control bytes so a crafted error cannot inject log lines or
 * terminal escape sequences. */
static void sanitize_log(char *s) {
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c < 0x20 || c == 0x7f) *s = '?';
    }
}

static void wait_interruptible_ms(int ms) {
    if (ms <= 0) return;
    int64_t deadline = now_mono_ms() + ms;
    while (!g_stop_main) {
        if (g_wake_reason) return;
        int64_t left = deadline - now_mono_ms();
        if (left <= 0) return;
        if (left > 1000) left = 1000;
        struct pollfd fds[2];
        int nfds = 0, wake_i = -1, stop_i = -1;
        if (g_wake_rd >= 0) {
            wake_i = nfds;
            fds[nfds].fd = g_wake_rd;
            fds[nfds].events = POLLIN;
            fds[nfds].revents = 0;
            nfds++;
        }
        if (g_main_stop_rd >= 0) {
            stop_i = nfds;
            fds[nfds].fd = g_main_stop_rd;
            fds[nfds].events = POLLIN;
            fds[nfds].revents = 0;
            nfds++;
        }
        int pr = poll(fds, nfds, (int)left);
        if (pr > 0) {
            if (wake_i >= 0 && (fds[wake_i].revents & POLLIN)) consume_wake();
            if (stop_i >= 0 && (fds[stop_i].revents & POLLIN)) record_main_stop();
        }
    }
}

int session_loop(void) {
    struct url base, u;
    if (parse_url(g_url, &base) != 0) die("invalid url");
    update_exec_veto = work_waiting;
    u = base;
    strncpy(u.path, "/agent/v1/connect", sizeof(u.path) - 1);
    char host[128], user[64];
    hostname_user(host, sizeof(host), user, sizeof(user));
    int fail_index = 0;
    int auth_backoff = 0;
    int ever_connected = 0;
    int attempts = 0;
    /* Rock-solid connections never see the top-of-loop check, so poll the
     * manifest periodically while connected (only while idle). First check
     * soon after connecting so restarts pick up releases quickly. */
    int64_t next_update_ms = now_mono_ms() + UPDATE_SOON_MS;
    /* Names this process to the server: jobs parked by a session of another
     * process can never be answered, so the server fails them at once. */
    char inst[17];
    unsigned long long r;
    if (getrandom(&r, sizeof(r), GRND_NONBLOCK) != (ssize_t)sizeof(r))
        r = ((unsigned long long)getpid() << 32) ^ (unsigned long long)now_mono_ms();
    snprintf(inst, sizeof(inst), "%016llx", r);
    start_watch_thread();
    while (!g_stop_main) {
        /* A stop that arrived before the first poll (e.g. during startup)
         * is still queued in the stop pipe: honor it before any network work. */
        if (g_main_stop_rd >= 0) {
            struct pollfd sp = {g_main_stop_rd, POLLIN, 0};
            if (poll(&sp, 1, 0) > 0 && (sp.revents & POLLIN)) {
                record_main_stop();
                break;
            }
        }
        attempts++;
        consume_wake();
        int wake = wake_take(); /* consume then take: no lost reset */
        /* Don't stall a resume/network reconnect on the auto-update HTTP fetch. */
        if (fail_index == 0 && wake == 0 && agent_idle()) maybe_update(1);
        if (wake == WAKE_RESUME) fprintf(stderr, "fuse-agent: resume detected\n");
        else if (wake == WAKE_NETWORK) fprintf(stderr, "fuse-agent: network up\n");
        if (ever_connected || attempts > 1)
            fprintf(stderr, "fuse-agent: reconnect attempt %d\n", attempts);

        struct tls t;
        if (tls_connect(&t, &u) != 0 || ws_handshake(&t, &u) != 0) {
            tls_close(&t);
            if (fail_index < 6) fail_index++;
            int wait_ms = reconnect_backoff_ms(fail_index, (unsigned)rand());
            fprintf(stderr, "fuse-agent: connect failed; retry in %dms\n", wait_ms);
            wait_interruptible_ms(wait_ms);
            continue;
        }
        cJSON *hello = cJSON_CreateObject();
        cJSON_AddNumberToObject(hello, "v", 1);
        cJSON_AddStringToObject(hello, "t", "hello");
        if (g_code[0]) cJSON_AddStringToObject(hello, "code", g_code);
        if (g_token[0]) cJSON_AddStringToObject(hello, "token", g_token);
        cJSON_AddStringToObject(hello, "hostname", host);
        cJSON_AddStringToObject(hello, "user", user);
        cJSON_AddStringToObject(hello, "os", "linux");
        cJSON_AddStringToObject(hello, "arch", arch_sku());
        cJSON_AddStringToObject(hello, "ver", FUSE_AGENT_VERSION);
        cJSON_AddStringToObject(hello, "inst", inst);
        char *hs = cJSON_PrintUnformatted(hello);
        cJSON_Delete(hello);
        if (!hs || ws_send_text(&t, hs) != 0) {
            free(hs);
            tls_close(&t);
            if (fail_index < 6) fail_index++;
            int wait_ms = reconnect_backoff_ms(fail_index, (unsigned)rand());
            fprintf(stderr, "fuse-agent: disconnected (hello failed); reconnecting\n");
            wait_interruptible_ms(wait_ms);
            continue;
        }
        free(hs);
        char *first = ws_read_text(&t, NULL, NULL);
        if (!first) {
            tls_close(&t);
            if (fail_index < 6) fail_index++;
            int wait_ms = reconnect_backoff_ms(fail_index, (unsigned)rand());
            fprintf(stderr, "fuse-agent: disconnected (no hello reply); reconnecting\n");
            wait_interruptible_ms(wait_ms);
            continue;
        }
        cJSON *reply = cJSON_Parse(first);
        free(first);
        const char *typ = json_cstr(reply, "t", "");
        if (strcmp(typ, "hello_ok") != 0) {
            const char *err = json_cstr(reply, "error", "auth failed");
            char errbuf[256];
            snprintf(errbuf, sizeof(errbuf), "%s", err);
            sanitize_log(errbuf);
            fprintf(stderr, "fuse-agent: %s\n", errbuf);
            cJSON_Delete(reply);
            tls_close(&t);
            /* err points into reply, freed above: classify the copy. */
            if (strstr(errbuf, "invalid") || strstr(errbuf, "expired") || strstr(errbuf, "token")) {
                /* Revoked or expired credential. Never hot-exit: systemd would
                   restart-loop. Retry slowly; the host may be re-enabled. */
                auth_backoff = auth_backoff < 60 ? 60 : (auth_backoff * 2 > 600 ? 600 : auth_backoff * 2);
                wait_interruptible_ms(auth_backoff * 1000);
                continue;
            }
            if (fail_index < 6) fail_index++;
            int wait_ms = reconnect_backoff_ms(fail_index, (unsigned)rand());
            wait_interruptible_ms(wait_ms);
            continue;
        }
        const char *tok = json_cstr(reply, "token", "");
        if (tok[0]) {
            /* Reject rather than truncate: a clipped token persisted to
             * config would auth-fail forever after the next restart. */
            if (strlen(tok) >= sizeof(g_token)) {
                fprintf(stderr, "fuse-agent: server token too long\n");
                cJSON_Delete(reply);
                tls_close(&t);
                wait_interruptible_ms(60000);
                continue;
            }
            strcpy(g_token, tok);
            g_code[0] = 0;
            if (g_config[0] && save_config(g_config) != 0)
                fprintf(stderr, "fuse-agent: could not save the device token to %s: %s; "
                        "this connector will need a new install code after it restarts\n",
                        g_config, strerror(errno));
        }
        {
            char alias[128];
            snprintf(alias, sizeof(alias), "%s", json_cstr(reply, "alias", "machine"));
            sanitize_log(alias);
            fprintf(stderr, "fuse-agent: connected as %s\n", alias);
        }
        cJSON_Delete(reply);
        auth_backoff = 0;
        attempts = 0;
        ever_connected = 1;
        next_update_ms = update_check_after_connect(next_update_ms, now_mono_ms());
        int replaced = 0;
        const char *drop_reason = NULL;
        int got_frame = 0;
        int64_t session_start = now_mono_ms();
        struct conn_watch watch;
        conn_watch_init(&watch, session_start, now_boot_ms());
        set_live_fd(t.fd);
        s_live_tls = &t;
        while (!g_stop_main) {
            int send_ping = 0;
            consume_wake();
            int wake_now = wake_take();
            drop_reason = conn_watch_tick(&watch, now_mono_ms(), now_boot_ms(), wake_now, &send_ping);
            if (drop_reason) break;
            if (send_ping) {
                static const char ping_json[] = "{\"v\":1,\"t\":\"ping\"}";
                if (ws_send(&t, 1, (const unsigned char *)ping_json, sizeof(ping_json) - 1) != 0) {
                    int werr = g_wake_reason;
                    drop_reason = (werr && wake_reason_str(werr)[0]) ? wake_reason_str(werr)
                                                                     : "io error";
                    break;
                }
                conn_watch_sent_ping(&watch, now_mono_ms());
            }
            if (out_flush(&t) != 0) {
                drop_reason = "io error";
                break;
            }
            if (g_auto_update && now_mono_ms() >= next_update_ms) {
                if (agent_idle()) {
                    maybe_update(1); /* execv on success; returns on failure */
                    /* A restart deferred by work that arrived mid-download
                     * is retried soon, not at the next full interval. */
                    next_update_ms = now_mono_ms() + (update_exec_pending() ? 60000 : UPDATE_EVERY_MS);
                } else {
                    /* Jobs in flight: retry soon rather than starve updates. */
                    next_update_ms = now_mono_ms() + 60000;
                }
            }
            if (SSL_pending(t.ssl) == 0 && t.pb_off >= t.pb_len) {
                struct pollfd p[3];
                int n = 1;
                int wake_i = -1, stop_i = -1;
                p[0].fd = t.fd;
                p[0].events = POLLIN;
                p[0].revents = 0;
                if (g_wake_rd >= 0) {
                    wake_i = n;
                    p[n].fd = g_wake_rd;
                    p[n].events = POLLIN;
                    p[n].revents = 0;
                    n++;
                }
                if (g_main_stop_rd >= 0) {
                    stop_i = n;
                    p[n].fd = g_main_stop_rd;
                    p[n].events = POLLIN;
                    p[n].revents = 0;
                    n++;
                }
                int pr = poll(p, n, conn_watch_wait_ms(&watch, now_mono_ms()));
                if (wake_i >= 0 && (p[wake_i].revents & POLLIN)) consume_wake();
                if (stop_i >= 0 && (p[stop_i].revents & POLLIN)) record_main_stop();
                if (pr < 0) {
                    if (errno == EINTR) continue;
                    drop_reason = "io error";
                    break;
                }
                if (g_stop_main) break; /* loop condition exits, then cleanup */
                if (g_wake_reason) continue;
                if (p[0].revents & (POLLERR | POLLHUP | POLLNVAL)) {
                    drop_reason = "socket closed";
                    break;
                }
                pthread_mutex_lock(&g_out_mu);
                int pending_out = g_out_n;
                pthread_mutex_unlock(&g_out_mu);
                if (pending_out) continue;
                if (pr == 0 || !(p[0].revents & POLLIN)) continue;
            }
            int opcode = 0;
            size_t n_in = 0;
            char *msg = ws_read_text(&t, &opcode, &n_in);
            if (!msg) {
                int wdrop = g_wake_reason;
                drop_reason = wdrop ? wake_reason_str(wdrop) : "socket closed";
                if (!drop_reason[0]) drop_reason = "socket closed";
                break;
            }
            conn_watch_got_rx(&watch, now_mono_ms());
            got_frame = 1;
            if (opcode == 9) {
                int prc = ws_send(&t, 10, (const unsigned char *)msg, n_in);
                free(msg);
                if (prc != 0) {
                    drop_reason = "io error";
                    break;
                }
                continue;
            }
            if (opcode == 10) {
                free(msg);
                continue;
            }
            cJSON *o = cJSON_Parse(msg);
            free(msg);
            if (!o) continue;
            const char *tt = json_cstr(o, "t", "");
            if (strcmp(tt, "job") == 0) {
                char jid[16] = {0};
                json_str(o, "id", jid, sizeof(jid));
                /* job_reserve rejects oversized ids; a truncated jid here
                 * would reserve under a different key than the wire id. */
                if (jid[0] && strlen(json_cstr(o, "id", "")) < sizeof(jid) && !job_admit(jid)) {
                    /* Duplicate live id (or no free slot): never share a
                     * slot with the job already running under it. */
                    send_res(&t, jid, 0, 1, "", "", "agent is busy");
                    cJSON_Delete(o);
                    continue;
                }
                enqueue_job(&t, o); /* ownership moves to the worker */
                o = NULL;
            } else if (strcmp(tt, "cancel") == 0) {
                 char cid[16];
                 const char *raw_id = json_cstr(o, "id", "");
                 if (strlen(raw_id) < sizeof(cid) && json_str(o, "id", cid, sizeof(cid)) == 0) {
                     pthread_mutex_lock(&g_job_mu);
                     for (int i = 0; i < MAX_JOBS; i++) {
                         if (strcmp(g_jobs[i].id, cid) == 0) {
                             g_jobs[i].cancel = 1;
                             if (g_jobs[i].pid > 0) kill(-g_jobs[i].pid, SIGKILL);
                         }
                     }
                     pthread_mutex_unlock(&g_job_mu);
                 }
             } else if (strcmp(tt, "bye") == 0) {
                if (strcmp(json_cstr(o, "reason", ""), "replaced") == 0) replaced = 1;
                drop_reason = replaced ? "replaced" : "server bye";
                cJSON_Delete(o);
                break;
            } else if (strcmp(tt, "ping") == 0) {
                static const char pong_json[] = "{\"v\":1,\"t\":\"pong\"}";
                if (ws_send(&t, 1, (const unsigned char *)pong_json, sizeof(pong_json) - 1) != 0) {
                    drop_reason = "io error";
                    cJSON_Delete(o);
                    break;
                }
            } else if (strcmp(tt, "pong") == 0) {
                /* liveness already recorded via conn_watch_got_rx */
            }
            cJSON_Delete(o);
        }
        set_live_fd(-1);
        s_live_tls = NULL;
        tls_close(&t);
        if (!g_stop_main) {
            if (replaced) {
                /* Another instance paired with the same token took over.
                   Retry slowly so two duplicates cannot kick each other. */
                int wait = 120 + rand() % 61;
                fprintf(stderr, "fuse-agent: replaced by another instance; retrying in %ds\n", wait);
                wait_interruptible_ms(wait * 1000);
                attempts = 0;
            } else {
                int wake_now = wake_take(); /* consume then take */
                if (wake_now == WAKE_RESUME || (drop_reason && strcmp(drop_reason, "resume") == 0))
                    fprintf(stderr, "fuse-agent: resume detected\n");
                else if (wake_now == WAKE_NETWORK ||
                         (drop_reason && strcmp(drop_reason, "network change") == 0))
                    fprintf(stderr, "fuse-agent: network up\n");
                fail_index = fail_index_after_session(fail_index, now_mono_ms() - session_start, got_frame);
                int wait_ms = reconnect_backoff_ms(fail_index, (unsigned)rand());
                fprintf(stderr, "fuse-agent: disconnected (%s); reconnecting in %dms\n",
                        drop_reason ? drop_reason : "socket closed", wait_ms);
                wait_interruptible_ms(wait_ms);
                if (!fail_index) attempts = 0;
            }
        }
    }
    return 0;
}

/* One-shot pair: connect, hello, save token, return. */
int pair_once(void) {
    struct url base, u;
    if (parse_url(g_url, &base) != 0) die("invalid url");
    u = base;
    strncpy(u.path, "/agent/v1/connect", sizeof(u.path) - 1);
    struct tls t;
    if (tls_connect(&t, &u) != 0) die("tls connect failed (need outbound HTTPS to %s)", g_url);
    if (ws_handshake(&t, &u) != 0) die("websocket handshake failed");
    char host[128], user[64];
    hostname_user(host, sizeof(host), user, sizeof(user));
    cJSON *hello = cJSON_CreateObject();
    cJSON_AddNumberToObject(hello, "v", 1);
    cJSON_AddStringToObject(hello, "t", "hello");
    if (g_code[0]) cJSON_AddStringToObject(hello, "code", g_code);
    if (g_token[0]) cJSON_AddStringToObject(hello, "token", g_token);
    cJSON_AddStringToObject(hello, "hostname", host);
    cJSON_AddStringToObject(hello, "user", user);
    cJSON_AddStringToObject(hello, "os", "linux");
    cJSON_AddStringToObject(hello, "arch", arch_sku());
    cJSON_AddStringToObject(hello, "ver", FUSE_AGENT_VERSION);
    char *hs = cJSON_PrintUnformatted(hello);
    cJSON_Delete(hello);
    if (!hs || ws_send_text(&t, hs) != 0) die("hello failed");
    free(hs);
    char *first = ws_read_text(&t, NULL, NULL);
    if (!first) die("no hello reply");
    cJSON *reply = cJSON_Parse(first);
    free(first);
    if (strcmp(json_cstr(reply, "t", ""), "hello_ok") != 0) {
        char errbuf[256];
        snprintf(errbuf, sizeof(errbuf), "%s", json_cstr(reply, "error", "pairing failed"));
        sanitize_log(errbuf);
        die("%s", errbuf);
    }
    const char *tok = json_cstr(reply, "token", "");
    if (tok[0]) {
        if (strlen(tok) >= sizeof(g_token)) die("server token too long");
        strcpy(g_token, tok);
    }
    g_code[0] = 0;
    {
        char alias[128];
        snprintf(alias, sizeof(alias), "%s", json_cstr(reply, "alias", "machine"));
        sanitize_log(alias);
        fprintf(stderr, "fuse-agent: paired as %s\n", alias);
    }
    cJSON_Delete(reply);
    tls_close(&t);
    return 0;
}
