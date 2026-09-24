/* Fuse connector: outbound WSS agent. OpenSSL + cJSON. */
#include <arpa/inet.h>
#include <ctype.h>
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <pwd.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/file.h>
#ifdef __linux__
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#endif

#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/sha.h>
#include <openssl/ssl.h>

#include "cJSON.h"
#include "reconnect.h"

#ifndef FUSE_AGENT_VERSION
#define FUSE_AGENT_VERSION "0.0.0"
#endif

#define OUT_CAP 65536
/* exec-family output cap: the server clips results to 6000 chars, so
 * capturing more than this is pure waste of RAM and bandwidth. */
#define EXEC_CAP 8192
/* Whole-file edit ceiling for the replace op. Only old/new travel over the
 * socket (never the file), so this is a RAM guard, not a transport limit.
 * Keep in sync with _REPLACE_FILE_MAX in app/workspace.py and editFileMax in
 * agent/windows/fs.go. */
#define EDIT_FILE_MAX (64L * 1024 * 1024)
/* write op content ceiling (server pre-checks 400000; transport-bounded). */
#define WRITE_FILE_MAX 400000L
#define UPDATE_EVERY_MS (60 * 60 * 1000)
#define WS_MAX (1024 * 1024)
#define WS_DRAIN_MAX (16 * 1024 * 1024)
#define MAX_JOBS 128
#define MAX_QUEUE 128
#define WORKERS 16
#define OUT_QMAX 128

struct url {
    char host[256];
    char path[256];
    int port;
};

struct tls {
    int fd;
    SSL_CTX *ctx;
    SSL *ssl;
    /* Bytes read past the end of the HTTP handshake header, stashed so the
     * first WebSocket frame is not lost if the server pipelined them. */
    unsigned char pb[512];
    size_t pb_len;
    size_t pb_off;
};

struct job {
    char id[16];
    pid_t pid;
    volatile sig_atomic_t cancel;
};

static struct job g_jobs[MAX_JOBS];
static pthread_mutex_t g_job_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_job_cv = PTHREAD_COND_INITIALIZER;
static cJSON *g_queue[MAX_QUEUE];
static int g_qhead = 0, g_qtail = 0, g_qcount = 0;
static int g_worker_started = 0;
static int g_busy = 0;
static struct {
    int opcode;
    unsigned char *data;
    size_t n;
} g_outq[OUT_QMAX];
static int g_out_head = 0, g_out_tail = 0, g_out_n = 0;
static pthread_mutex_t g_out_mu = PTHREAD_MUTEX_INITIALIZER;
static char g_url[256] = {0};
static char g_token[192] = {0};
static char g_code[128] = {0};
static char g_config[PATH_MAX] = {0};
static int g_auto_update = 0;
/* Wake/stop state crosses threads and signal contexts ONLY through pipes:
 * write(2) is async-signal-safe, and a single reader (the session-loop
 * thread) derives the flags below from the bytes. No shared variable is
 * written cross-thread, so correctness never depends on lock-free atomics.
 * Shared wake pipe bytes: 1 = resume, 2 = network change, 0 = pure wakeup.
 * Stop does NOT travel on the wake pipe: out_wake can fill it with zeros and
 * a nonblocking stop write would then hit EAGAIN and be lost forever. Stop
 * is level-triggered on dedicated, unsaturable stop pipes that only on_sig
 * writes; record_main_stop() drains one while setting the sticky flag. */
static int g_stop_main = 0;    /* main-thread only; set via stop pipe */
static int g_wake_reason = 0;  /* main-thread only; latest wake reason */
static pthread_mutex_t g_conn_mu = PTHREAD_MUTEX_INITIALIZER;
static int g_live_fd = -1;
static int g_wake_rd = -1;
static int g_wake_wr = -1;
static int g_main_stop_rd = -1;
static int g_main_stop_wr = -1;
static int g_watch_stop_rd = -1;
static int g_watch_stop_wr = -1;
static int g_watch_started = 0;

static void die(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fputs("fuse-agent: ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
    exit(1);
}

static void on_sig(int sig) {
    (void)sig;
    /* Dedicated stop pipes carry the signal: only this handler writes them,
     * so they cannot saturate and a stop can never be lost to EAGAIN the way
     * it would on the shared wake pipe (which out_wake can fill with zeros).
     * The wake-pipe byte is best-effort: it only speeds up the poll wakeup;
     * the reason it may carry would degrade to a ping-timeout reconnect. */
    char stop = 1, wake = 0;
    if (g_main_stop_wr >= 0) {
        (void)!write(g_main_stop_wr, &stop, 1);
    }
    if (g_watch_stop_wr >= 0) {
        (void)!write(g_watch_stop_wr, &stop, 1);
    }
    if (g_wake_wr >= 0) {
        (void)!write(g_wake_wr, &wake, 1);
    }
}

static void on_usr1(int sig) {
    (void)sig;
    char b = 1;
    if (g_wake_wr >= 0) {
        (void)!write(g_wake_wr, &b, 1);
    }
}

/* Single consumer: the session-loop thread. Derives the latest wake reason
 * from the shared wake pipe bytes. */
static void consume_wake(void) {
    if (g_wake_rd < 0) return;
    char buf[64];
    ssize_t k;
    while ((k = read(g_wake_rd, buf, sizeof(buf))) > 0) {
        for (ssize_t i = 0; i < k; i++) {
            switch (buf[i]) {
            case 1: g_wake_reason = WAKE_RESUME; break;
            case 2: g_wake_reason = WAKE_NETWORK; break;
            default: break;
            }
        }
    }
}

/* Level-triggered stop: drain the dedicated stop pipe while recording the
 * sticky flag. Only on_sig writes this pipe, so it cannot saturate; even if
 * several signals queued, one drain records them all. */
static void record_main_stop(void) {
    if (g_main_stop_rd >= 0) {
        char buf[64];
        while (read(g_main_stop_rd, buf, sizeof(buf)) > 0) {
        }
    }
    g_stop_main = 1;
}

/* Atomically consume-and-clear the pending wake reason (main thread only,
 * so no lost reset is possible against producers: they only write bytes). */
static int wake_take(void) {
    int r = g_wake_reason;
    g_wake_reason = 0;
    return r;
}

static void set_live_fd(int fd) {
    pthread_mutex_lock(&g_conn_mu);
    g_live_fd = fd;
    pthread_mutex_unlock(&g_conn_mu);
}

static void request_reconnect(int reason) {
    char b = (reason == WAKE_RESUME) ? 1 : 2;
    /* Validate and shut the fd down under the connection mutex: a copied fd
     * could be closed and re-used by a fresh connection before the shutdown
     * landed. close() only happens after set_live_fd(-1) under this same
     * mutex, so a value read here is either still the live connection or -1;
     * a recycled number can only be re-registered after that transition. */
    pthread_mutex_lock(&g_conn_mu);
    int fd = g_live_fd;
    if (fd >= 0) shutdown(fd, SHUT_RDWR);
    pthread_mutex_unlock(&g_conn_mu);
    if (g_wake_wr >= 0) {
        (void)!write(g_wake_wr, &b, 1);
    }
}

static void trim(char *s) {
    size_t n = strlen(s);
    while (n && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ')) s[--n] = 0;
}

static int parse_url(const char *raw, struct url *u) {
    memset(u, 0, sizeof(*u));
    if (strncmp(raw, "https://", 8) != 0) return -1;
    const char *p = raw + 8;
    const char *slash = strchr(p, '/');
    const char *colon = strchr(p, ':');
    size_t hostlen;
    u->port = 443;
    if (colon && (!slash || colon < slash)) {
        hostlen = (size_t)(colon - p);
        u->port = atoi(colon + 1);
        if (u->port <= 0 || u->port > 65535) return -1;
    } else {
        hostlen = slash ? (size_t)(slash - p) : strlen(p);
    }
    if (hostlen == 0 || hostlen >= sizeof(u->host)) return -1;
    memcpy(u->host, p, hostlen);
    u->host[hostlen] = 0;
    if (slash) {
        if (strlen(slash) >= sizeof(u->path)) return -1;
        strncpy(u->path, slash, sizeof(u->path) - 1);
    } else {
        strcpy(u->path, "/");
    }
    return 0;
}

static int set_nonblock(int fd, int on) {
    int fl = fcntl(fd, F_GETFL, 0);
    if (fl < 0) return -1;
    if (on) fl |= O_NONBLOCK;
    else fl &= ~O_NONBLOCK;
    return fcntl(fd, F_SETFL, fl);
}

static void tune_sock(int fd) {
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &one, sizeof(one));
#ifdef TCP_KEEPIDLE
    {
        int idle = 30, intvl = 10, cnt = 3;
        setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE, &idle, sizeof(idle));
        setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &intvl, sizeof(intvl));
        setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, &cnt, sizeof(cnt));
    }
#endif
#ifdef TCP_USER_TIMEOUT
    {
        int uto = 20000;
        setsockopt(fd, IPPROTO_TCP, TCP_USER_TIMEOUT, &uto, sizeof(uto));
    }
#endif
#ifdef TCP_NODELAY
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
#endif
}

static int wait_fd(int fd, int events, int timeout_ms) {
    int64_t deadline = now_mono_ms() + (timeout_ms < 0 ? 0 : timeout_ms);
    for (;;) {
        int left = timeout_ms;
        if (timeout_ms >= 0) {
            left = (int)(deadline - now_mono_ms());
            if (left <= 0) return 0;
        }
        struct pollfd p[3];
        int n = 1;
        int wake_i = -1, stop_i = -1;
        p[0].fd = fd;
        p[0].events = (short)events;
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
        int pr = poll(p, n, left);
        if (wake_i >= 0 && (p[wake_i].revents & POLLIN)) consume_wake();
        if (stop_i >= 0 && (p[stop_i].revents & POLLIN)) record_main_stop();
        if (pr < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (g_wake_reason || g_stop_main) return -2;
        if (pr == 0) return 0;
        if (p[0].revents & (POLLERR | POLLHUP | POLLNVAL)) {
            if (p[0].revents & events) return 1;
            return -1;
        }
        if (p[0].revents & events) return 1;
    }
}

static int wait_ssl(struct tls *t, int ssl_err, int timeout_ms) {
    int events = (ssl_err == SSL_ERROR_WANT_WRITE) ? POLLOUT : POLLIN;
    return wait_fd(t->fd, events, timeout_ms);
}

static int connect_timeout(int fd, const struct sockaddr *sa, socklen_t len, int timeout_ms) {
    int rc = connect(fd, sa, len);
    if (rc == 0) return 0;
    if (errno != EINPROGRESS) return -1;
    int w = wait_fd(fd, POLLOUT, timeout_ms);
    if (w <= 0) return -1;
    int err = 0;
    socklen_t elen = sizeof(err);
    if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &elen) != 0 || err != 0) return -1;
    return 0;
}

static int tls_connect(struct tls *t, const struct url *u) {
    memset(t, 0, sizeof(*t));
    t->fd = -1;
    struct addrinfo hints, *res = NULL, *rp;
    char port[8];
    snprintf(port, sizeof(port), "%d", u->port);
    memset(&hints, 0, sizeof(hints));
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_family = AF_UNSPEC;
    if (getaddrinfo(u->host, port, &hints, &res) != 0) return -1;
    int fd = -1;
    for (rp = res; rp; rp = rp->ai_next) {
        fd = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
        if (fd < 0) continue;
        fcntl(fd, F_SETFD, FD_CLOEXEC);
        set_nonblock(fd, 1);
        tune_sock(fd);
        if (connect_timeout(fd, rp->ai_addr, rp->ai_addrlen, CONNECT_TIMEOUT_MS) == 0) break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) return -1;
    SSL_CTX *ctx = SSL_CTX_new(TLS_client_method());
    if (!ctx) {
        close(fd);
        return -1;
    }
    SSL_CTX_set_verify(ctx, SSL_VERIFY_PEER, NULL);
    SSL_CTX_set_default_verify_paths(ctx);
    SSL_CTX_set_min_proto_version(ctx, TLS1_2_VERSION);
    SSL *ssl = SSL_new(ctx);
    SSL_set_fd(ssl, fd);
    SSL_set_tlsext_host_name(ssl, u->host);
    SSL_set1_host(ssl, u->host);
    int64_t deadline = now_mono_ms() + CONNECT_TIMEOUT_MS;
    for (;;) {
        int rc = SSL_connect(ssl);
        if (rc == 1) break;
        int err = SSL_get_error(ssl, rc);
        if (err != SSL_ERROR_WANT_READ && err != SSL_ERROR_WANT_WRITE) {
            SSL_free(ssl);
            SSL_CTX_free(ctx);
            close(fd);
            return -1;
        }
        int64_t left = deadline - now_mono_ms();
        int events = (err == SSL_ERROR_WANT_WRITE) ? POLLOUT : POLLIN;
        if (left <= 0 || wait_fd(fd, events, (int)left) <= 0) {
            SSL_free(ssl);
            SSL_CTX_free(ctx);
            close(fd);
            return -1;
        }
    }
    t->fd = fd;
    t->ctx = ctx;
    t->ssl = ssl;
    return 0;
}

static void tls_close(struct tls *t) {
    if (t->fd >= 0) {
        pthread_mutex_lock(&g_conn_mu);
        if (t->fd == g_live_fd) g_live_fd = -1;
        pthread_mutex_unlock(&g_conn_mu);
    }
    if (t->ssl) {
        SSL_set_quiet_shutdown(t->ssl, 1);
        SSL_shutdown(t->ssl);
        SSL_free(t->ssl);
    }
    if (t->ctx) SSL_CTX_free(t->ctx);
    if (t->fd >= 0) close(t->fd);
    memset(t, 0, sizeof(*t));
    t->fd = -1;
}

static int tls_write_all(struct tls *t, const void *buf, size_t n) {
    const unsigned char *p = buf;
    int64_t deadline = now_mono_ms() + IO_TIMEOUT_MS;
    while (n) {
        if (g_wake_reason || g_stop_main) return -1;
        int k = SSL_write(t->ssl, p, (int)n);
        if (k > 0) {
            p += k;
            n -= (size_t)k;
            continue;
        }
        int err = SSL_get_error(t->ssl, k);
        if (err == SSL_ERROR_WANT_READ || err == SSL_ERROR_WANT_WRITE) {
            int64_t left = deadline - now_mono_ms();
            if (left <= 0) return -1;
            if (wait_ssl(t, err, (int)left) <= 0) return -1;
            continue;
        }
        return -1;
    }
    return 0;
}

static int tls_read_some(struct tls *t, void *buf, size_t n) {
    for (;;) {
        int k = SSL_read(t->ssl, buf, (int)n);
        if (k > 0) return k;
        int err = SSL_get_error(t->ssl, k);
        if (err == SSL_ERROR_WANT_READ || err == SSL_ERROR_WANT_WRITE) {
            if (wait_ssl(t, err, IO_TIMEOUT_MS) <= 0) return -1;
            continue;
        }
        return k;
    }
}

static int b64(const unsigned char *in, int inlen, char *out, int outcap) {
    if (outcap < 4 * ((inlen + 2) / 3) + 1) return -1;
    if (EVP_EncodeBlock((unsigned char *)out, in, inlen) < 0) return -1;
    return 0;
}

static int ws_handshake(struct tls *t, const struct url *u) {
    unsigned char keyraw[16];
    char keyb64[32];
    if (RAND_bytes(keyraw, 16) != 1) return -1;
    if (b64(keyraw, 16, keyb64, sizeof(keyb64)) != 0) return -1;
    char acc_in[128];
    unsigned char md[EVP_MAX_MD_SIZE];
    unsigned int mdlen = 0;
    char expect[64];
    snprintf(acc_in, sizeof(acc_in), "%s258EAFA5-E914-47DA-95CA-C5AB0DC85B11", keyb64);
    if (EVP_Digest(acc_in, strlen(acc_in), md, &mdlen, EVP_sha1(), NULL) != 1) return -1;
    if (b64(md, (int)mdlen, expect, sizeof(expect)) != 0) return -1;
    char req[1024];
    int n = snprintf(req, sizeof(req),
                     "GET /agent/v1/connect HTTP/1.1\r\n"
                     "Host: %s\r\n"
                     "Upgrade: websocket\r\n"
                     "Connection: Upgrade\r\n"
                     "Sec-WebSocket-Key: %s\r\n"
                     "Sec-WebSocket-Version: 13\r\n"
                     "User-Agent: fuse-agent/%s\r\n"
                     "\r\n",
                     u->host, keyb64, FUSE_AGENT_VERSION);
    if (n < 0 || n >= (int)sizeof(req)) return -1;
    if (tls_write_all(t, req, (size_t)n) != 0) return -1;
    char buf[2048];
    size_t got = 0;
    size_t hdr_end = 0;
    while (got < sizeof(buf) - 1) {
        int k = tls_read_some(t, buf + got, sizeof(buf) - 1 - got);
        if (k <= 0) return -1;
        got += (size_t)k;
        buf[got] = 0;
        char *p = strstr(buf, "\r\n\r\n");
        if (p) {
            hdr_end = (size_t)(p - buf) + 4;
            break;
        }
    }
    if (strncmp(buf, "HTTP/1.1 101", 12) != 0 && strncmp(buf, "HTTP/1.0 101", 12) != 0) return -1;
    char *acc = strcasestr(buf, "sec-websocket-accept:");
    if (!acc) return -1;
    acc += strlen("sec-websocket-accept:");
    while (*acc == ' ' || *acc == '\t') acc++;
    size_t alen = strcspn(acc, "\r\n");
    if (alen != strlen(expect) || strncmp(acc, expect, alen) != 0) return -1;
    /* Keep any bytes the server pipelined after the handshake response. */
    if (got > hdr_end) {
        size_t extra = got - hdr_end;
        if (extra > sizeof(t->pb)) extra = sizeof(t->pb);
        memcpy(t->pb, buf + hdr_end, extra);
        t->pb_len = extra;
        t->pb_off = 0;
    }
    return 0;
}

static int ws_send(struct tls *t, int opcode, const unsigned char *data, size_t n) {
    unsigned char hdr[14];
    unsigned char mask[4];
    size_t hlen = 2;
    hdr[0] = (unsigned char)(0x80u | (opcode & 0xf));
    if (n < 126) {
        hdr[1] = (unsigned char)(0x80u | n);
    } else if (n <= 0xffff) {
        hdr[1] = 0x80 | 126;
        hdr[2] = (unsigned char)((n >> 8) & 0xff);
        hdr[3] = (unsigned char)(n & 0xff);
        hlen = 4;
    } else {
        hdr[1] = 0x80 | 127;
        memset(hdr + 2, 0, 6);
        hdr[8] = (unsigned char)((n >> 24) & 0xff);
        hdr[9] = (unsigned char)((n >> 16) & 0xff);
        hdr[10] = (unsigned char)((n >> 8) & 0xff);
        hdr[11] = (unsigned char)(n & 0xff);
        hlen = 10;
    }
    if (RAND_bytes(mask, 4) != 1) return -1;
    memcpy(hdr + hlen, mask, 4);
    hlen += 4;
    if (tls_write_all(t, hdr, hlen) != 0) return -1;
    unsigned char tmp[4096];
    size_t off = 0;
    while (off < n) {
        size_t chunk = n - off;
        if (chunk > sizeof(tmp)) chunk = sizeof(tmp);
        for (size_t i = 0; i < chunk; i++) tmp[i] = data[off + i] ^ mask[(off + i) & 3];
        if (tls_write_all(t, tmp, chunk) != 0) return -1;
        off += chunk;
    }
    return 0;
}

static int ws_send_text(struct tls *t, const char *s) {
    return ws_send(t, 1, (const unsigned char *)s, strlen(s));
}

static void out_wake(void) {
    /* Pure wakeup: byte 0 carries no wake reason. */
    if (g_wake_wr >= 0) {
        char x = 0;
        (void)!write(g_wake_wr, &x, 1);
    }
}

static int out_enqueue(int opcode, const unsigned char *data, size_t n) {
    unsigned char *copy = malloc(n ? n : 1);
    if (!copy) return -1;
    if (n) memcpy(copy, data, n);
    pthread_mutex_lock(&g_out_mu);
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

static int out_flush(struct tls *t) {
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
        pthread_mutex_unlock(&g_out_mu);
        free(data);
    }
}

static int ssl_read_n(struct tls *t, unsigned char *buf, size_t n) {
    size_t got = 0;
    int64_t deadline = now_mono_ms() + IO_TIMEOUT_MS;
    while (got < n) {
        if (t->pb_off < t->pb_len) {
            size_t take = t->pb_len - t->pb_off;
            if (take > n - got) take = n - got;
            memcpy(buf + got, t->pb + t->pb_off, take);
            t->pb_off += take;
            got += take;
            continue;
        }
        if (g_wake_reason || g_stop_main) return -1;
        int k = SSL_read(t->ssl, buf + got, (int)(n - got));
        if (k > 0) {
            got += (size_t)k;
            continue;
        }
        int err = SSL_get_error(t->ssl, k);
        if (err == SSL_ERROR_WANT_READ || err == SSL_ERROR_WANT_WRITE) {
            int64_t left = deadline - now_mono_ms();
            if (left <= 0) return -1;
            if (wait_ssl(t, err, (int)left) <= 0) return -1;
            continue;
        }
        return -1;
    }
    return 0;
}

static char *ws_read_text(struct tls *t, int *opcode_out, size_t *n_out) {
    unsigned char *acc = NULL;
    size_t acc_len = 0;
    int acc_op = -1;
    for (;;) {
        unsigned char h[2];
        if (ssl_read_n(t, h, 2) != 0) {
            free(acc);
            return NULL;
        }
        int fin = (h[0] & 0x80) != 0;
        int opcode = h[0] & 0x0f;
        int masked = (h[1] & 0x80) != 0;
        uint64_t n = h[1] & 0x7f;
        if (n == 126) {
            unsigned char ext[2];
            if (ssl_read_n(t, ext, 2) != 0) {
                free(acc);
                return NULL;
            }
            n = ((uint64_t)ext[0] << 8) | ext[1];
        } else if (n == 127) {
            unsigned char ext[8];
            if (ssl_read_n(t, ext, 8) != 0) {
                free(acc);
                return NULL;
            }
            n = 0;
            for (int i = 0; i < 8; i++) n = (n << 8) | ext[i];
        }
        if (opcode == 8) {
            free(acc);
            return NULL;
        }
        if (n > WS_MAX) {
            /* Oversized frame: drop any partial message, then skip this one
             * frame (not the whole session) when it is small enough to drain. */
            free(acc);
            acc = NULL;
            acc_len = 0;
            acc_op = -1;
            if (n > WS_DRAIN_MAX) return NULL;
            unsigned char dmask[4] = {0};
            if (masked && ssl_read_n(t, dmask, 4) != 0) return NULL;
            unsigned char sink[4096];
            while (n) {
                size_t chunk = n < sizeof(sink) ? (size_t)n : sizeof(sink);
                if (ssl_read_n(t, sink, chunk) != 0) return NULL;
                n -= chunk;
            }
            fprintf(stderr, "fuse-agent: skipped oversized frame\n");
            continue;
        }
        unsigned char mask[4] = {0};
        if (masked && ssl_read_n(t, mask, 4) != 0) {
            free(acc);
            return NULL;
        }
        unsigned char *payload = malloc(n + 1);
        if (!payload) {
            free(acc);
            return NULL;
        }
        if (n && ssl_read_n(t, payload, (size_t)n) != 0) {
            free(payload);
            free(acc);
            return NULL;
        }
        if (masked) {
            for (uint64_t i = 0; i < n; i++) payload[i] ^= mask[i & 3];
        }
        payload[n] = 0;
        if (opcode == 9) {
            /* Answer pings inline so a fragmented message can survive an
             * interleaved control frame (RFC 6455 5.5.1 allows this). */
            int prc = ws_send(t, 10, payload, (size_t)n);
            free(payload);
            if (prc != 0) {
                free(acc);
                return NULL;
            }
            continue;
        }
        if (opcode == 10) {
            free(payload);
            continue;
        }
        if (opcode == 1 || opcode == 2) {
            if (acc) {
                free(payload);
                free(acc);
                return NULL;
            }
            acc_op = opcode;
        } else if (opcode == 0) {
            if (acc_op < 0) {
                free(payload);
                free(acc);
                return NULL;
            }
        } else {
            free(payload);
            continue;
        }
        unsigned char *grown = realloc(acc, acc_len + (size_t)n + 1);
        if (!grown) {
            free(payload);
            free(acc);
            return NULL;
        }
        acc = grown;
        memcpy(acc + acc_len, payload, (size_t)n);
        free(payload);
        acc_len += (size_t)n;
        acc[acc_len] = 0;
        if (fin) {
            if (n_out) *n_out = acc_len;
            if (opcode_out) *opcode_out = acc_op;
            return (char *)acc;
        }
    }
}

static int json_str(cJSON *o, const char *k, char *dst, size_t cap) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (!cJSON_IsString(v) || !v->valuestring) return -1;
    strncpy(dst, v->valuestring, cap - 1);
    dst[cap - 1] = 0;
    return 0;
}

static const char *json_cstr(cJSON *o, const char *k, const char *fallback) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (cJSON_IsString(v) && v->valuestring) return v->valuestring;
    return fallback;
}

static int json_int(cJSON *o, const char *k, int fallback) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (cJSON_IsNumber(v)) return v->valueint;
    return fallback;
}

static int json_bool(cJSON *o, const char *k) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    return cJSON_IsTrue(v);
}

static void send_chunk(const char *id, const char *stream, const char *data, size_t n) {
    if (!data || !n) return;
    char *tmp = malloc(n + 1);
    if (!tmp) return;
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
    if (s) {
        out_enqueue(1, (const unsigned char *)s, strlen(s));
        free(s);
    }
}

static void send_res_tmo(struct tls *t, const char *id, int ok, int exit_code, const char *out, const char *err, const char *error, int timed_out) {
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
    int clipped = (out && strstr(out, "[fuse-agent: output truncated]")) ||
                  (err && strstr(err, "[fuse-agent: output truncated]"));
    cJSON_AddBoolToObject(o, "truncated", clipped);
     char *s = cJSON_PrintUnformatted(o);
     cJSON_Delete(o);
     if (s) {
         (void)t;
         out_enqueue(1, (const unsigned char *)s, strlen(s));
         free(s);
     }
}

static void send_res(struct tls *t, const char *id, int ok, int exit_code, const char *out, const char *err, const char *error) {
    send_res_tmo(t, id, ok, exit_code, out, err, error, 0);
}

static struct job *job_reserve(const char *id) {
    if (!id || !id[0]) return NULL;
    pthread_mutex_lock(&g_job_mu);
    for (int i = 0; i < MAX_JOBS; i++) {
        if (g_jobs[i].id[0] && strcmp(g_jobs[i].id, id) == 0) {
            pthread_mutex_unlock(&g_job_mu);
            return &g_jobs[i];
        }
    }
    for (int i = 0; i < MAX_JOBS; i++) {
        if (!g_jobs[i].id[0]) {
            memset(&g_jobs[i], 0, sizeof(g_jobs[i]));
            strncpy(g_jobs[i].id, id, sizeof(g_jobs[i].id) - 1);
            pthread_mutex_unlock(&g_job_mu);
            return &g_jobs[i];
        }
    }
    pthread_mutex_unlock(&g_job_mu);
    return NULL;
}

static void job_clear(struct job *j) {
    if (!j) return;
    pthread_mutex_lock(&g_job_mu);
    memset(j, 0, sizeof(*j));
    pthread_mutex_unlock(&g_job_mu);
}

static int job_cancelled(struct job *j) {
    pthread_mutex_lock(&g_job_mu);
    int cancelled = j && j->cancel;
    pthread_mutex_unlock(&g_job_mu);
    return cancelled;
}

static int run_exec(const char *cwd, const char *cmd, int timeout_ms, char *out, size_t ocap, char *errb, size_t ecap, int *code, struct job *job, int *timed_out, int stream, const char *job_id) {
    int outp[2], errp[2];
    /* O_CLOEXEC: exec'd commands (and daemons they spawn) must not inherit
     * job pipe fds. dup2() in the child clears CLOEXEC on stdout/stderr. */
    if (pipe2(outp, O_CLOEXEC) != 0) return -1;
    if (pipe2(errp, O_CLOEXEC) != 0) {
        close(outp[0]);
        close(outp[1]);
        return -1;
    }
    pid_t pid = fork();
    if (pid < 0) {
        close(outp[0]);
        close(outp[1]);
        close(errp[0]);
        close(errp[1]);
        return -1;
    }
    if (pid == 0) {
        setpgid(0, 0);
        /* The agent ignores SIGPIPE for its own socket writes and SIG_IGN
         * survives fork+exec; children need the default back or `| head`
         * pipelines end in an EPIPE "write error" exit 2 instead of 141. */
        signal(SIGPIPE, SIG_DFL);
        int chdir_err = 0;
        if (cwd && cwd[0] && chdir(cwd) != 0) chdir_err = errno;
        dup2(outp[1], 1);
        dup2(errp[1], 2);
        close(outp[0]);
        close(outp[1]);
        close(errp[0]);
        close(errp[1]);
        if (chdir_err) {
            dprintf(2, "fuse-agent: chdir %s: %s\n", cwd, strerror(chdir_err));
            _exit(127);
        }
        execl("/bin/bash", "bash", "--noprofile", "--norc", "-c", cmd ? cmd : "", (char *)NULL);
        dprintf(2, "fuse-agent: exec bash: %s\n", strerror(errno));
        _exit(127);
    }
    setpgid(pid, pid);
    if (job) {
        pthread_mutex_lock(&g_job_mu);
        job->pid = pid;
        int cancel = job->cancel;
        pthread_mutex_unlock(&g_job_mu);
        if (cancel) kill(-pid, SIGKILL);
    }
    close(outp[1]);
    close(errp[1]);
    fcntl(outp[0], F_SETFL, O_NONBLOCK);
    fcntl(errp[0], F_SETFL, O_NONBLOCK);
    size_t o = 0, e = 0;
    if (ocap) out[0] = 0;
    if (ecap) errb[0] = 0;
    struct timespec start;
    clock_gettime(CLOCK_MONOTONIC, &start);
    int status = 0, done = 0, killed = 0, by_timeout = 0;
    int out_clipped = 0, err_clipped = 0;
    long kill_ms = 0;
    while (done < 2) {
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        long elapsed = (now.tv_sec - start.tv_sec) * 1000L + (now.tv_nsec - start.tv_nsec) / 1000000L;
        if (!done && !killed && job_cancelled(job)) {
            kill(-pid, SIGKILL);
            killed = 1;
            kill_ms = elapsed;
        }
        if (!done && !killed && timeout_ms > 0 && elapsed >= timeout_ms) {
            kill(-pid, SIGKILL);
            killed = 1;
            by_timeout = 1;
            kill_ms = elapsed;
        }
        /* SIGKILL is not enough for D-state (NFS hang, ptrace): never let an
         * unkillable child wedge a worker thread forever. Abandon the reap;
         * the slot is released either way and one zombie is the lesser evil. */
        if (killed && elapsed - kill_ms >= 10000) {
            fprintf(stderr, "fuse-agent: child %d unkillable after SIGKILL; abandoning\n", (int)pid);
            close(outp[0]);
            close(errp[0]);
            if (code) *code = -1; /* no reap: remote outcome remains unconfirmed */
            if (timed_out) *timed_out = by_timeout;
            return 0;
        }
        struct pollfd pfd[2] = {{outp[0], POLLIN, 0}, {errp[0], POLLIN, 0}};
        poll(pfd, 2, 100);
        char tmp[2048];
        ssize_t k;
        for (int reads = 0; reads < 64 && (k = read(outp[0], tmp, sizeof(tmp))) > 0; reads++) {
            if (stream) send_chunk(job_id, "out", tmp, (size_t)k);
            if (o + (size_t)k >= ocap) { out_clipped = 1; k = (ssize_t)(ocap - 1 - o); }
            if (k > 0 && !stream) {
                memcpy(out + o, tmp, (size_t)k);
                o += (size_t)k;
                out[o] = 0;
            }
        }
        for (int reads = 0; reads < 64 && (k = read(errp[0], tmp, sizeof(tmp))) > 0; reads++) {
            if (stream) send_chunk(job_id, "err", tmp, (size_t)k);
            if (e + (size_t)k >= ecap) { err_clipped = 1; k = (ssize_t)(ecap - 1 - e); }
            if (k > 0 && !stream) {
                memcpy(errb + e, tmp, (size_t)k);
                e += (size_t)k;
                errb[e] = 0;
            }
        }
        if (done) { done = 2; continue; } /* final bounded drain after reap */
        pthread_mutex_lock(&g_job_mu);
        pid_t w = waitpid(pid, &status, WNOHANG);
        if (w == pid && job) job->pid = 0; /* do not signal a re-used pid after reap */
        pthread_mutex_unlock(&g_job_mu);
        if (w == pid) done = 1;
    }
    close(outp[0]);
    close(errp[0]);
    if (!by_timeout && timeout_ms > 0 && !job_cancelled(job)) {
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        long elapsed = (now.tv_sec - start.tv_sec) * 1000L + (now.tv_nsec - start.tv_nsec) / 1000000L;
        if (elapsed >= timeout_ms) by_timeout = 1;
    }
    const char *mark = "\n[fuse-agent: output truncated]\n";
    size_t marklen = strlen(mark);
    if (out_clipped && ocap > marklen + 1) {
        size_t end = ocap - marklen - 1;
        while (end && ((unsigned char)out[end] & 0xc0) == 0x80) end--;
        strcpy(out + end, mark);
    }
    if (err_clipped && ecap > marklen + 1) {
        size_t end = ecap - marklen - 1;
        while (end && ((unsigned char)errb[end] & 0xc0) == 0x80) end--;
        strcpy(errb + end, mark);
    }
    if (WIFEXITED(status)) *code = WEXITSTATUS(status);
    else *code = 1;
    if (timed_out) *timed_out = by_timeout;
    return 0;
}

static int expand_home(const char *path, char *out, size_t cap);

static int read_file_slice(const char *path, int offset, int limit, char *out, size_t cap) {
    char expanded[PATH_MAX];
    if (expand_home(path, expanded, sizeof(expanded)) != 0) return -1;
    path = expanded;
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    struct stat st;
    if (fstat(fileno(f), &st) != 0 || !S_ISREG(st.st_mode)) {
        fclose(f);
        errno = EINVAL;
        return -1;
    }
    if (offset < 1) offset = 1;
    if (limit < 1) limit = 2000;
    if (limit > 2000) limit = 2000;
    static const char mark[] = "\n[fuse-agent: output truncated]\n";
    const size_t marklen = sizeof(mark) - 1;
    const size_t content_cap = (cap > marklen + 1) ? cap - 1 - marklen : 0;
    char line[8192];
    int lineno = 1, kept = 0, truncated = 0;
    size_t used = 0;
    size_t scanned = 0;
    out[0] = 0;
    size_t ln;
    while (kept < limit && (ln = fread(line, 1, sizeof(line), f)) > 0) {
        scanned += ln;
        if (scanned > 64 * 1024 * 1024) {
            fclose(f);
            errno = EOVERFLOW;  /* distinct from the output byte cap (EFBIG) */
            return -1;
        }
        for (size_t i = 0; i < ln && kept < limit; i++) {
            if (line[i] == 0) {
                fclose(f);
                errno = EINVAL;
                return -1;
            }
            if (lineno >= offset) {
                if (used < content_cap) {
                    out[used++] = line[i];
                } else {
                    truncated = 1;
                }
                if (line[i] == '\n') kept++;
            }
            if (line[i] == '\n') lineno++;
        }
    }
    out[used] = 0;
    int failed = ferror(f);
    fclose(f);
    if (truncated) {
        out[0] = 0;
        errno = EFBIG;
        return -1;
    }
    return failed ? -1 : 0;
}

static void handle_job(struct tls *t, cJSON *msg);

static int write_file_atomic_limit(const char *path, const char *content, long max) {
    if (content && (long)strlen(content) > max) { errno = EFBIG; return -1; }
    char expanded[PATH_MAX];
    if (expand_home(path, expanded, sizeof(expanded)) != 0) return -1;
    path = expanded;
    char tmp[PATH_MAX];
    char dir[PATH_MAX];
    const char *slash = strrchr(path, '/');
    if (slash) {
        size_t n = (size_t)(slash - path);
        if (n >= sizeof(dir)) return -1;
        memcpy(dir, path, n);
        dir[n] = 0;
        if (n == 0) strcpy(dir, "/");
    } else {
        strcpy(dir, ".");
    }
    if (snprintf(tmp, sizeof(tmp), "%s/.fuse-XXXXXX", dir) >= (int)sizeof(tmp)) return -1;
    int fd = mkstemp(tmp);
    if (fd < 0) return -1;
    struct stat st;
    mode_t mode = 0600;
    if (lstat(path, &st) == 0) {
        if (!S_ISREG(st.st_mode)) {
            close(fd);
            unlink(tmp);
            errno = EINVAL;
            return -1;
        }
        mode = st.st_mode & 0777;
    } else if (errno != ENOENT) {
        close(fd);
        unlink(tmp);
        return -1;
    }
    if (fchmod(fd, mode) != 0) {
        close(fd);
        unlink(tmp);
        return -1;
    }
    size_t n = content ? strlen(content) : 0;
    size_t done = 0;
    while (done < n) {
        ssize_t wrote = write(fd, content + done, n - done);
        if (wrote < 0 && errno == EINTR) continue;
        if (wrote <= 0) {
            close(fd);
            unlink(tmp);
            return -1;
        }
        done += (size_t)wrote;
    }
    if (fsync(fd) != 0) {
        close(fd);
        unlink(tmp);
        return -1;
    }
    if (close(fd) != 0) {
        unlink(tmp);
        return -1;
    }
    if (rename(tmp, path) != 0) {
        unlink(tmp);
        return -1;
    }
    /* Make the rename itself durable: fsync the parent directory. */
    int dfd = open(dir, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (dfd >= 0) {
        (void)fsync(dfd);
        close(dfd);
    }
    return 0;
}

static int write_file_atomic(const char *path, const char *content) {
    return write_file_atomic_limit(path, content, WRITE_FILE_MAX);
}

static const char *home_dir(void) {
    const char *h = getenv("HOME");
    if (h && h[0]) return h;
    struct passwd *pw = getpwuid(getuid());
    if (pw && pw->pw_dir && pw->pw_dir[0]) return pw->pw_dir;
    return "/";
}

static int expand_home(const char *path, char *out, size_t cap) {
    if (!path || !path[0]) path = "~";
    const char *rest = NULL;
    if (path[0] == '~') rest = path + 1;
    else if (strncmp(path, "$HOME", 5) == 0) rest = path + 5;
    if (!rest) {
        if (strlen(path) >= cap) return -1;
        memcpy(out, path, strlen(path) + 1);
        return 0;
    }
    if (rest[0] && rest[0] != '/') {
        if (strlen(path) >= cap) return -1;
        memcpy(out, path, strlen(path) + 1);
        return 0;
    }
    const char *home = home_dir();
    if (snprintf(out, cap, "%s%s", home, rest) >= (int)cap) return -1;
    return 0;
}

static int expand_dir(const char *path, char *out, size_t cap) {
    char tmp[PATH_MAX];
    if (expand_home(path, tmp, sizeof(tmp)) != 0) return -1;
    if (!realpath(tmp, out)) {
        if (strlen(tmp) >= cap) return -1;
        memcpy(out, tmp, strlen(tmp) + 1);
    }
    struct stat st;
    if (stat(out, &st) != 0 || !S_ISDIR(st.st_mode)) return -1;
    return 0;
}

static char *browse_json(const char *path) {
    char resolved[PATH_MAX];
    if (expand_dir(path, resolved, sizeof(resolved)) != 0) return NULL;
    DIR *d = opendir(resolved);
    if (!d) return NULL;
    cJSON *root = cJSON_CreateObject();
    cJSON *arr = cJSON_CreateArray();
    cJSON_AddStringToObject(root, "path", resolved);
    cJSON_AddStringToObject(root, "home", home_dir());
    if (strcmp(resolved, "/") == 0) {
        cJSON_AddStringToObject(root, "parent", "");
    } else {
        char parent[PATH_MAX];
        strncpy(parent, resolved, sizeof(parent) - 1);
        parent[sizeof(parent) - 1] = 0;
        char *slash = strrchr(parent, '/');
        if (slash == parent) parent[1] = 0;
        else if (slash) *slash = 0;
        cJSON_AddStringToObject(root, "parent", parent);
    }
    typedef struct {
        char *name;
        int dir;
    } item;
    item items[400];
    int n = 0;
    struct dirent *ent;
    while ((ent = readdir(d)) && n < 400) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) continue;
        if (strchr(ent->d_name, '/')) continue;
        int isdir = 0;
#ifdef DT_DIR
        if (ent->d_type == DT_DIR) isdir = 1;
        else if (ent->d_type == DT_UNKNOWN || ent->d_type == DT_LNK)
#endif
        {
            char full[PATH_MAX];
            if (snprintf(full, sizeof(full), "%s/%s", resolved, ent->d_name) >= (int)sizeof(full)) continue;
            struct stat st;
            if (stat(full, &st) == 0 && S_ISDIR(st.st_mode)) isdir = 1;
#ifdef DT_DIR
            else if (ent->d_type == DT_DIR) isdir = 1;
#endif
        }
        items[n].name = strdup(ent->d_name);
        items[n].dir = isdir;
        if (items[n].name) n++;
    }
    closedir(d);
    for (int i = 0; i < n; i++) {
        for (int j = i + 1; j < n; j++) {
            if (items[j].dir != items[i].dir) {
                if (items[j].dir && !items[i].dir) {
                    item x = items[i];
                    items[i] = items[j];
                    items[j] = x;
                }
            } else if (strcmp(items[i].name, items[j].name) > 0) {
                item x = items[i];
                items[i] = items[j];
                items[j] = x;
            }
        }
    }
    for (int i = 0; i < n; i++) {
        cJSON *row = cJSON_CreateObject();
        cJSON_AddStringToObject(row, "name", items[i].name);
        cJSON_AddStringToObject(row, "kind", items[i].dir ? "dir" : "file");
        cJSON_AddItemToArray(arr, row);
        free(items[i].name);
    }
    cJSON_AddItemToObject(root, "entries", arr);
    char *js = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return js;
}

static int entry_is_dir(const char *dir, const char *name, unsigned char dtype) {
#ifdef DT_DIR
    if (dtype == DT_DIR) return 1;
    if (dtype != DT_UNKNOWN && dtype != DT_LNK) return 0;
#endif
    char buf[PATH_MAX];
    struct stat st;
    int n = snprintf(buf, sizeof(buf), "%s/%s", dir, name);
    if (n < 0 || (size_t)n >= sizeof(buf)) return 0;
    if (stat(buf, &st) != 0) return 0;
    return S_ISDIR(st.st_mode);
}

static int list_dir(const char *path, char *out, size_t cap) {
    char expanded[PATH_MAX];
    if (expand_home(path, expanded, sizeof(expanded)) != 0) return -1;
    path = expanded;
    DIR *d = opendir(path);
    if (!d) return -1;
    struct dirent *ent;
    size_t used = 0;
    out[0] = 0;
    char *names[400];
    int n = 0;
    while ((ent = readdir(d)) && n < 400) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) continue;
        size_t ln = strlen(ent->d_name);
        int dir = entry_is_dir(path, ent->d_name, ent->d_type);
        char *copy = malloc(ln + 2);
        if (!copy) continue;
        memcpy(copy, ent->d_name, ln);
        if (dir) {
            copy[ln] = '/';
            copy[ln + 1] = 0;
        } else {
            copy[ln] = 0;
        }
        names[n++] = copy;
    }
    closedir(d);
    for (int i = 0; i < n; i++) {
        for (int j = i + 1; j < n; j++) {
            if (strcmp(names[i], names[j]) > 0) {
                char *x = names[i];
                names[i] = names[j];
                names[j] = x;
            }
        }
    }
    for (int i = 0; i < n; i++) {
        size_t ln = strlen(names[i]);
        if (used + ln + 2 >= cap) break;
        memcpy(out + used, names[i], ln);
        used += ln;
        out[used++] = '\n';
        out[used] = 0;
    }
    for (int i = 0; i < n; i++) free(names[i]);
    return 0;
}

static int glob_ok(const char *s) {
    size_t n = strlen(s);
    if (n == 0 || n > 200) return 0;
    if (s[0] == '/' || s[0] == '~' || (n >= 2 && s[0] == '.' && s[1] == '.')) return 0;
    if (strstr(s, "/..")) return 0;
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              strchr("_.*?/[]{}!,-", c)))
            return 0;
    }
    return 1;
}

static int load_whole(const char *path, char **buf, size_t *len) {
    char expanded[PATH_MAX];
    if (expand_home(path, expanded, sizeof(expanded)) != 0) return -1;
    path = expanded;
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    if (fseek(f, 0, SEEK_END) != 0) {
        fclose(f);
        return -1;
    }
    long sz = ftell(f);
    if (sz < 0 || sz > EDIT_FILE_MAX) {
        fclose(f);
        /* Callers translate errno into the actual cause; a bare failure
         * sends the model into a blind retry loop. */
        errno = sz > 0 ? EFBIG : EIO;
        return -1;
    }
    rewind(f);
    char *p = malloc((size_t)sz + 1);
    if (!p) {
        fclose(f);
        return -1;
    }
    if (sz && fread(p, 1, (size_t)sz, f) != (size_t)sz) {
        free(p);
        fclose(f);
        return -1;
    }
    p[sz] = 0;
    if (memchr(p, 0, (size_t)sz) || fgetc(f) != EOF || ferror(f)) {
        free(p);
        fclose(f);
        errno = EINVAL;
        return -1;
    }
    fclose(f);
    *buf = p;
    *len = (size_t)sz;
    return 0;
}

/* Expand LF to CRLF in a NUL-terminated string (edit-argument repair for
 * CRLF files; read_file output hides carriage returns). NULL on OOM. */
static char *crlf_expand(const char *s, size_t n) {
    size_t nl = 0;
    for (size_t i = 0; i < n; i++)
        if (s[i] == '\n') nl++;
    char *out = malloc(n + nl + 1);
    if (!out) return NULL;
    char *w = out;
    for (size_t i = 0; i < n; i++) {
        if (s[i] == '\n' && (i == 0 || s[i - 1] != '\r')) *w++ = '\r';
        *w++ = s[i];
    }
    *w = 0;
    return out;
}

static void *job_worker(void *arg) {
    (void)arg;
    for (;;) {
        pthread_mutex_lock(&g_job_mu);
        while (g_qcount == 0) pthread_cond_wait(&g_job_cv, &g_job_mu);
        cJSON *o = g_queue[g_qhead];
        g_queue[g_qhead] = NULL;
        g_qhead = (g_qhead + 1) % MAX_QUEUE;
        g_qcount--;
        g_busy++;
        pthread_mutex_unlock(&g_job_mu);
        if (o) {
            handle_job(NULL, o);
            cJSON_Delete(o);
        }
        pthread_mutex_lock(&g_job_mu);
        g_busy--;
        pthread_mutex_unlock(&g_job_mu);
    }
    return NULL;
}

static void ensure_workers(void) {
    if (g_worker_started) return;
    for (int i = 0; i < WORKERS; i++) {
        pthread_t th;
        if (pthread_create(&th, NULL, job_worker, NULL) == 0) {
            pthread_detach(th);
            g_worker_started++;
        }
    }
}

/* Takes ownership of `owned` (a parsed job object); parses it exactly once. */
static void enqueue_job(cJSON *owned) {
    ensure_workers();
    pthread_mutex_lock(&g_job_mu);
    if (g_qcount >= MAX_QUEUE || g_qcount + g_busy >= MAX_JOBS) {
        pthread_mutex_unlock(&g_job_mu);
        char id[16] = {0};
        if (owned) json_str(owned, "id", id, sizeof(id));
        cJSON_Delete(owned);
        job_clear(job_reserve(id));
        send_res(NULL, id, 0, 1, "", "", "agent is busy");
        return;
    }
    g_queue[g_qtail] = owned;
    g_qtail = (g_qtail + 1) % MAX_QUEUE;
    g_qcount++;
    pthread_cond_signal(&g_job_cv);
    pthread_mutex_unlock(&g_job_mu);
}

static void handle_job_impl(struct tls *t, cJSON *msg);

static void handle_job(struct tls *t, cJSON *msg) {
    static pthread_mutex_t mutation_mu = PTHREAD_MUTEX_INITIALIZER;
    const char *op = json_cstr(msg, "op", "");
    int mutation = strcmp(op, "write") == 0 || strcmp(op, "replace") == 0;
    if (mutation) pthread_mutex_lock(&mutation_mu);
    const char *id = json_cstr(msg, "id", "");
    struct job *job = job_reserve(id);
    pthread_mutex_lock(&g_job_mu);
    int cancelled = job && job->cancel;
    pthread_mutex_unlock(&g_job_mu);
    if (!job) send_res(t, id, 0, 1, "", "", "agent is busy");
    else if (cancelled) send_res(t, id, 0, 1, "", "", "cancelled");
    else handle_job_impl(t, msg);
    /* Every operation reserves a slot, not just exec. */
    job_clear(job);
    if (mutation) pthread_mutex_unlock(&mutation_mu);
}

static void handle_job_impl(struct tls *t, cJSON *msg) {
    char id[16] = {0};
    json_str(msg, "id", id, sizeof(id));
    const char *op = json_cstr(msg, "op", "");
    char out[OUT_CAP];
    char err[OUT_CAP];
    out[0] = err[0] = 0;
    int code = 0;
    if (strcmp(op, "cancel") == 0) return;
    if (strcmp(op, "exec") == 0 || strcmp(op, "exec_stream") == 0) {
        int stream = strcmp(op, "exec_stream") == 0;
        const char *cwd = json_cstr(msg, "cwd", "~");
        const char *cmd = json_cstr(msg, "cmd", "");
        int timeout = json_int(msg, "timeout_ms", 30000);
        char cwd_exp[PATH_MAX];
        if (expand_home(cwd, cwd_exp, sizeof(cwd_exp)) != 0) {
            send_res(t, id, 0, 127, "", "", "invalid cwd");
            return;
        }
        cwd = cwd_exp;
        struct job *job = job_reserve(id);
        if (job_cancelled(job)) {
            send_res(t, id, 0, 1, "", "", "cancelled");
            return;
        }
        int timed_out = 0;
        if (run_exec(cwd, cmd, timeout, out, EXEC_CAP, err, EXEC_CAP, &code, job, &timed_out, stream, id) != 0) {
            send_res(t, id, 0, 1, "", "", "exec failed");
            return;
        }
        {
            char ebuf[240];
            const char *emsg = "";
            ebuf[0] = 0;
            if (code != 0) {
                const char *src = err[0] ? err : out;
                if (src && src[0]) {
                    strncpy(ebuf, src, sizeof(ebuf) - 1);
                    ebuf[sizeof(ebuf) - 1] = 0;
                    char *nl = strchr(ebuf, '\n');
                    if (nl) *nl = 0;
                    emsg = ebuf;
                } else {
                    snprintf(ebuf, sizeof(ebuf), "exit %d", code);
                    emsg = ebuf;
                }
            }
            if (timed_out) emsg = "timed out";
            send_res_tmo(t, id, code == 0, code, out, err, emsg, timed_out);
        }
        return;
    }
    if (strcmp(op, "read") == 0) {
        const char *path = json_cstr(msg, "path", "");
        if (read_file_slice(path, json_int(msg, "offset", 1), json_int(msg, "limit", 200), out, sizeof(out)) != 0) {
            const char *why;
            if (errno == EINVAL)
                why = "file is not UTF-8 text (contains NUL bytes)";
            else if (errno == EFBIG)
                why = "read output exceeds 65536 bytes; use a smaller limit or grep";
            else if (errno == EOVERFLOW)
                why = "read scan exceeds 64 MiB; narrow the offset or use grep";
            else
                why = strerror(errno);
            send_res(t, id, 0, 1, "", why, "read failed");
        }
        else
            send_res(t, id, 1, 0, out, "", "");
        return;
    }
    if (strcmp(op, "write") == 0) {
        const char *path = json_cstr(msg, "path", "");
        const char *content = json_cstr(msg, "content", "");
        if (write_file_atomic(path, content) != 0) {
            char why[256];
            if (errno == EFBIG)
                snprintf(why, sizeof(why), "content exceeds %ld bytes; split the write", WRITE_FILE_MAX);
            else if (errno == EINVAL)
                snprintf(why, sizeof(why), "write target must be a regular file");
            else
                snprintf(why, sizeof(why), "write failed: %s", strerror(errno));
            send_res(t, id, 0, 1, "", why, "write failed");
        }
        else send_res(t, id, 1, 0, "wrote", "", "");
        return;
    }
    if (strcmp(op, "list") == 0) {
        if (list_dir(json_cstr(msg, "path", "."), out, sizeof(out)) != 0) {
            char why[256];
            snprintf(why, sizeof(why), "list failed: %s", strerror(errno));
            send_res(t, id, 0, 1, "", why, "list failed");
        }
        else send_res(t, id, 1, 0, out, "", "");
        return;
    }
    if (strcmp(op, "browse") == 0) {
        char *js = browse_json(json_cstr(msg, "path", "~"));
        if (!js) send_res(t, id, 0, 1, "", "", "browse failed");
        else {
            send_res(t, id, 1, 0, js, "", "");
            free(js);
        }
        return;
    }
    if (strcmp(op, "grep") == 0) {
        const char *path = json_cstr(msg, "path", ".");
        const char *pattern = json_cstr(msg, "pattern", "");
        if (strchr(pattern, '\'') || strchr(path, '\'') ||
            strlen(pattern) > 200 || strlen(path) > 300) {
            send_res(t, id, 0, 1, "", "", "invalid pattern or path");
            return;
        }
        /* Never `rg || grep`: rg exits 1 on no match, and grep -R then walks
         * node_modules/venv with no ignore file. Cap output with head so rg
         * gets SIGPIPE instead of searching the rest of the tree. */
        char line[2048];
        int w = snprintf(line, sizeof(line),
                         "set -o pipefail; if command -v rg >/dev/null 2>&1; then "
                         "rg -n --no-heading --color=never --max-filesize 1M -m 20 "
                         "--glob '!node_modules' --glob '!.git' --glob '!venv' --glob '!.venv' "
                         "--glob '!dist' --glob '!build' --glob '!__pycache__' --glob '!target' "
                         "-e '%s' -- '%s' | head -n 200; "
                         "else grep -R -n --binary-files=without-match "
                         "--exclude-dir=node_modules --exclude-dir=.git --exclude-dir=venv "
                         "--exclude-dir=.venv --exclude-dir=dist --exclude-dir=build "
                         "--exclude-dir=__pycache__ --exclude-dir=target -- '%s' '%s' | head -n 200; "
                         "fi",
                         pattern, path, pattern, path);
        if (w < 0 || (size_t)w >= sizeof(line)) {
            send_res(t, id, 0, 1, "", "", "pattern or path too long");
            return;
        }
        if (run_exec(".", line, 12000, out, EXEC_CAP, err, EXEC_CAP, &code, NULL, NULL, 0, NULL) != 0)
            send_res(t, id, 0, 1, "", "", "grep failed");
        else if (code != 0 && code != 1 && code != 141)
            send_res(t, id, 0, code, out, err, "grep failed");
        else
            send_res(t, id, 1, code, out, "", "");
        return;
    }
    if (strcmp(op, "glob") == 0) {
        const char *path = json_cstr(msg, "path", ".");
        const char *pattern = json_cstr(msg, "pattern", "");
        if (strchr(path, '\'') || strchr(pattern, '\'') || strlen(path) > 300 || !glob_ok(pattern)) {
            send_res(t, id, 0, 1, "", "", "invalid pattern");
            return;
        }
        /* find(1) fallback must honor the pattern too: -name for basename
         * globs, -path for patterns that span directories. */
        char find_expr[1024];
        int fw;
        if (strchr(pattern, '/')) {
            fw = snprintf(find_expr, sizeof(find_expr),
                          "find '%s' \\( -name node_modules -o -name .git -o -name venv -o -name .venv "
                          "-o -name dist -o -name __pycache__ -o -name build -o -name target \\) -prune "
                          "-o -path '%s/%s' -type f -print",
                          path, path, pattern);
        } else {
            fw = snprintf(find_expr, sizeof(find_expr),
                          "find '%s' \\( -name node_modules -o -name .git -o -name venv -o -name .venv "
                          "-o -name dist -o -name __pycache__ -o -name build -o -name target \\) -prune "
                          "-o -name '%s' -type f -print",
                          path, pattern);
        }
        if (fw < 0 || (size_t)fw >= sizeof(find_expr)) {
            send_res(t, id, 0, 1, "", "", "pattern or path too long");
            return;
        }
        char line[3072];
        int w = snprintf(line, sizeof(line),
                         "set -o pipefail; if command -v rg >/dev/null 2>&1; then "
                         "rg --files --color=never --glob '!node_modules' --glob '!.git' "
                         "--glob '!venv' --glob '!.venv' --glob '!dist' --glob '!__pycache__' "
                         "-g '%s' -- '%s' | head -n 400; "
                         "else %s | head -n 400; "
                         "fi",
                         pattern, path, find_expr);
        if (w < 0 || (size_t)w >= sizeof(line)) {
            send_res(t, id, 0, 1, "", "", "pattern or path too long");
            return;
        }
        if (run_exec(".", line, 12000, out, EXEC_CAP, err, EXEC_CAP, &code, NULL, NULL, 0, NULL) != 0)
            send_res(t, id, 0, 1, "", "", "glob failed");
        else if (code != 0 && code != 1 && code != 141)
            send_res(t, id, 0, code, out, err, "glob failed");
        else
            send_res(t, id, 1, 0, out, "", "");
        return;
    }
    if (strcmp(op, "replace") == 0) {
        const char *path = json_cstr(msg, "path", "");
        const char *old = json_cstr(msg, "old", "");
        const char *newv = json_cstr(msg, "new", "");
        int all = json_bool(msg, "replace_all");
        char *buf = NULL, *old_crlf = NULL, *new_crlf = NULL;
        size_t len = 0;
        char expanded[PATH_MAX];
        struct stat before, after;
        if (expand_home(path, expanded, sizeof(expanded)) != 0) {
            send_res(t, id, 0, 1, "", "", "invalid path");
            return;
        }
        if (lstat(expanded, &before) != 0) {
            send_res(t, id, 0, 1, "", "", errno == ENOENT ? "file does not exist" : "edit target is not readable");
            return;
        }
        if (!S_ISREG(before.st_mode)) {
            send_res(t, id, 0, 1, "", "", "edit target must be a regular file");
            return;
        }
        if (!old[0]) {
            send_res(t, id, 0, 1, "", "", "old_string required");
            return;
        }
        if (load_whole(path, &buf, &len) != 0) {
            char why[288];
            if (errno == EFBIG)
                snprintf(why, sizeof(why),
                         "file too large to edit (maximum %ld bytes); apply the change with run_terminal_command",
                         EDIT_FILE_MAX);
            else if (errno == EINVAL)
                snprintf(why, sizeof(why), "file is not UTF-8 text (contains NUL bytes)");
            else
                snprintf(why, sizeof(why), "read failed: %s", strerror(errno));
            send_res(t, id, 0, 1, "", why, "replace failed");
            return;
        }
        const char *old_use = old, *new_use = newv;
        size_t oldn = strlen(old), newn = strlen(newv);
        long count = 0;
        const char *p = buf;
        while ((p = strstr(p, old))) {
            count++;
            p += oldn;
        }
        if (count == 0 && memmem(buf, len, "\r\n", 2) && !memchr(old, '\r', oldn) && memchr(old, '\n', oldn)) {
            /* read_file hides carriage returns, so an LF-only old_string can
             * never match a CRLF file byte-for-byte. Convert LF to CRLF:
             * deterministic line-ending repair, never a fuzzy match. */
            old_crlf = crlf_expand(old, oldn);
            new_crlf = crlf_expand(newv, newn);
            if (old_crlf && new_crlf) {
                size_t o2 = strlen(old_crlf);
                const char *q = buf;
                while ((q = strstr(q, old_crlf))) {
                    count++;
                    q += o2;
                }
                if (count) {
                    old_use = old_crlf;
                    new_use = new_crlf;
                    oldn = o2;
                    newn = strlen(new_crlf);
                }
            }
        }
        if (count == 0) {
            free(buf); free(old_crlf); free(new_crlf);
            send_res(t, id, 0, 1, "", "", "old_string not found");
            return;
        }
        if (count > 1 && !all) {
            free(buf); free(old_crlf); free(new_crlf);
            send_res(t, id, 0, 1, "", "", "old_string is not unique");
            return;
        }
        /* Publication caps at EDIT_FILE_MAX bytes; check the exact final size
         * up front instead of failing after building the buffer. */
        long reps = all ? count : 1;
        long final = (long)len + reps * ((long)newn - (long)oldn);
        if (final > EDIT_FILE_MAX) {
            free(buf); free(old_crlf); free(new_crlf);
            char why[320];
            snprintf(why, sizeof(why),
                     "replacement too large (maximum %ld bytes); apply the change with run_terminal_command",
                     EDIT_FILE_MAX);
            send_res(t, id, 0, 1, "", "", why);
            return;
        }
        size_t nlen = (size_t)(final > (long)len ? final : (long)len) + 8;
        char *nb = malloc(nlen);
        if (!nb) {
            free(buf); free(old_crlf); free(new_crlf);
            send_res(t, id, 0, 1, "", "", "out of memory");
            return;
        }
        char *w = nb;
        const char *r = buf;
        long left = reps;
        while (left) {
            const char *hit = strstr(r, old_use);
            if (!hit) break;
            size_t pre = (size_t)(hit - r);
            memcpy(w, r, pre);
            w += pre;
            memcpy(w, new_use, newn);
            w += newn;
            r = hit + oldn;
            left--;
        }
        strcpy(w, r);
        if (lstat(expanded, &after) != 0 || before.st_dev != after.st_dev ||
            before.st_ino != after.st_ino || before.st_size != after.st_size ||
            before.st_mtim.tv_sec != after.st_mtim.tv_sec || before.st_mtim.tv_nsec != after.st_mtim.tv_nsec) {
            free(buf); free(nb); free(old_crlf); free(new_crlf);
            send_res(t, id, 0, 1, "", "", "file changed during edit; read it again");
            return;
        }
        int wr = write_file_atomic_limit(path, nb, EDIT_FILE_MAX);
        free(buf);
        free(nb);
        free(old_crlf);
        free(new_crlf);
        if (wr != 0) {
            char why[288];
            if (errno == EFBIG)
                snprintf(why, sizeof(why), "replacement too large (maximum %ld bytes)", EDIT_FILE_MAX);
            else
                snprintf(why, sizeof(why), "write failed: %s", strerror(errno));
            send_res(t, id, 0, 1, "", why, "replace failed");
        }
        else {
            snprintf(out, sizeof(out), "%ld replacement(s)", reps);
            send_res(t, id, 1, 0, out, "", "");
        }
        return;
    }
    if (strcmp(op, "kill") == 0) {
        const char *kid = json_cstr(msg, "id", "");
        pthread_mutex_lock(&g_job_mu);
        for (int i = 0; i < MAX_JOBS; i++) {
            if (g_jobs[i].id[0] && strcmp(g_jobs[i].id, kid) == 0) {
                g_jobs[i].cancel = 1;
                if (g_jobs[i].pid > 0) kill(-g_jobs[i].pid, SIGKILL);
            }
        }
        pthread_mutex_unlock(&g_job_mu);
        send_res(t, id, 1, 0, "cancelled", "", "");
        return;
    }
    send_res(t, id, 0, 1, "", "", "unknown op");
}

static int load_config(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        trim(line);
        if (!line[0] || line[0] == '#') continue;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = 0;
        const char *k = line;
        const char *v = eq + 1;
        if (strcmp(k, "url") == 0) strncpy(g_url, v, sizeof(g_url) - 1);
        else if (strcmp(k, "token") == 0) strncpy(g_token, v, sizeof(g_token) - 1);
        else if (strcmp(k, "auto_update") == 0) g_auto_update = atoi(v);
    }
    fclose(f);
    return 0;
}

static int save_config(const char *path) {
    char dir[PATH_MAX];
    if (snprintf(dir, sizeof(dir), "%s", path) >= (int)sizeof(dir)) return -1;
    char *slash = strrchr(dir, '/');
    if (slash) {
        *slash = 0;
        mkdir(dir, 0700);
    }
    /* Create 0600 from the start: fopen("w") would use 0666&umask and only
     * fchmod afterwards, leaving a window where the token is readable. */
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) return -1;
    fchmod(fd, 0600);
    FILE *f = fdopen(fd, "w");
    if (!f) {
        close(fd);
        return -1;
    }
    fprintf(f, "url=%s\n", g_url);
    fprintf(f, "token=%s\n", g_token);
    fprintf(f, "auto_update=%d\n", g_auto_update);
    int bad = ferror(f);
    fclose(f);
    return bad ? -1 : 0;
}

static void default_config_path(char *dst, size_t cap, int system_wide) {
    if (system_wide) {
        snprintf(dst, cap, "/etc/fuse-agent/agent.conf");
        return;
    }
    const char *home = getenv("HOME");
    if (!home) {
        struct passwd *pw = getpwuid(getuid());
        home = pw ? pw->pw_dir : ".";
    }
    snprintf(dst, cap, "%s/.config/fuse-agent/agent.conf", home);
}

static void hostname_user(char *host, size_t hcap, char *user, size_t ucap) {
    if (gethostname(host, hcap) != 0) strncpy(host, "machine", hcap - 1);
    host[hcap - 1] = 0;
    struct passwd *pw = getpwuid(getuid());
    strncpy(user, pw && pw->pw_name ? pw->pw_name : "user", ucap - 1);
    user[ucap - 1] = 0;
}

static const char *arch_sku(void) {
#if defined(__aarch64__)
    return "linux-arm64";
#else
    return "linux-amd64";
#endif
}

static int http_get_file(const char *base, const char *path, const char *dest, size_t max_bytes) {
    char full[512];
    snprintf(full, sizeof(full), "%s%s", base, path);
    struct url u;
    if (parse_url(full, &u) != 0) return -1;
    struct tls t;
    if (tls_connect(&t, &u) != 0) return -1;
    char req[640];
    int n = snprintf(req, sizeof(req), "GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n", u.path, u.host);
    if (n < 0 || (size_t)n >= sizeof(req)) {
        tls_close(&t);
        return -1;
    }
    if (tls_write_all(&t, req, (size_t)n) != 0) {
        tls_close(&t);
        return -1;
    }
    char hdr[4096];
    size_t got = 0;
    int hdr_done = 0;
    size_t hdr_end = 0;
    while (got < sizeof(hdr) - 1 && !hdr_done) {
        int k = tls_read_some(&t, hdr + got, sizeof(hdr) - 1 - got);
        if (k <= 0) break;
        got += (size_t)k;
        hdr[got] = 0;
        char *p = strstr(hdr, "\r\n\r\n");
        if (p) {
            hdr_done = 1;
            hdr_end = (size_t)(p - hdr) + 4;
        }
    }
    if (!hdr_done || strncmp(hdr, "HTTP/1.1 200", 12) != 0) {
        tls_close(&t);
        return -1;
    }
    FILE *f = fopen(dest, "wb");
    if (!f) {
        tls_close(&t);
        return -1;
    }
    size_t total = 0;
    int too_big = 0;
    if (got > hdr_end) {
        size_t add = got - hdr_end;
        if (total + add > max_bytes) too_big = 1;
        else if (fwrite(hdr + hdr_end, 1, add, f) != add) too_big = 1;
        else total += add;
    }
    char buf[8192];
    int k;
    while (!too_big && (k = tls_read_some(&t, buf, sizeof(buf))) > 0) {
        if (total + (size_t)k > max_bytes) {
            too_big = 1;
            break;
        }
        if (fwrite(buf, 1, (size_t)k, f) != (size_t)k) {
            too_big = 1;
            break;
        }
        total += (size_t)k;
    }
    if (too_big || ferror(f)) {
        fclose(f);
        unlink(dest);
        tls_close(&t);
        return -1;
    }
    fflush(f);
    fsync(fileno(f));
    fclose(f);
    tls_close(&t);
    return 0;
}

static int sha256_file(const char *path, char *hex, size_t cap) {
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    EVP_MD_CTX *ctx = EVP_MD_CTX_new();
    unsigned char md[EVP_MAX_MD_SIZE];
    unsigned int mdlen = 0;
    if (!ctx || EVP_DigestInit_ex(ctx, EVP_sha256(), NULL) != 1) {
        if (ctx) EVP_MD_CTX_free(ctx);
        fclose(f);
        return -1;
    }
    char buf[8192];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0) EVP_DigestUpdate(ctx, buf, n);
    fclose(f);
    EVP_DigestFinal_ex(ctx, md, &mdlen);
    EVP_MD_CTX_free(ctx);
    if (cap < mdlen * 2 + 1) return -1;
    for (unsigned int i = 0; i < mdlen; i++) sprintf(hex + i * 2, "%02x", md[i]);
    return 0;
}

static int ver_parse(const char *s, int v[3]) {
    int n = 0;
    while (n < 3) {
        char *end;
        long x = strtol(s, &end, 10);
        if (end == s || x < 0 || x > 1000000) return -1;
        v[n++] = (int)x;
        if (*end != '.') break;
        s = end + 1;
    }
    while (n < 3) v[n++] = 0;
    return 0;
}

/* Strictly newer only: never downgrade on an equal or lower remote version. */
static int ver_newer(const char *cand, const char *cur) {
    int a[3], b[3];
    if (ver_parse(cand, a) != 0) return 0;
    if (ver_parse(cur, b) != 0) return 1;
    for (int i = 0; i < 3; i++) {
        if (a[i] != b[i]) return a[i] > b[i];
    }
    return 0;
}

/* Cross-process update ownership: exactly one updater at a time (the daemon's
 * idle-gated auto-update or a manual `fuse-agent update`). The lock file is
 * created once and never unlinked while held, so contenders always flock the
 * same inode; release is close(). */
static int update_lock_acquire(const char *self, char *lockpath, size_t cap) {
    if (snprintf(lockpath, cap, "%s.update.lock", self) >= (int)cap) return -1;
    int fd = open(lockpath, O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
    if (fd < 0) return -1;
    if (flock(fd, LOCK_EX | LOCK_NB) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

/* Exclusive-create staging name. The update lock already serializes updaters,
 * but the staging file itself is still created O_EXCL (mkstemp) so a leftover
 * from a crashed updater can never be re-opened and truncated by the next
 * run: verification then always covers bytes this process alone wrote. */
static int format_update_staging(char *dst, size_t cap, const char *self) {
    size_t n = strlen(self);
    if (n + sizeof(".new.XXXXXX") > cap) return -1;
    memcpy(dst, self, n);
    memcpy(dst + n, ".new.XXXXXX", sizeof(".new.XXXXXX"));
    return 0;
}

/* allow_exec: only the connected daemon may execv() into the new build - it
 * replaces itself (same PID, single daemon) and only from the idle-gated
 * call sites. A manual `fuse-agent update` runs in its own process and must
 * never become a second live daemon (two instances on one token replace each
 * other forever), so it stages the verified binary and leaves restart
 * ownership to the operator or systemd. */
static int maybe_update(int allow_exec) {
    if (!g_auto_update || !g_url[0]) return 0;
    char self[PATH_MAX];
    ssize_t k = readlink("/proc/self/exe", self, sizeof(self) - 1);
    if (k < 0) return 0;
    self[k] = 0;
    char lockpath[PATH_MAX];
    int lockfd = update_lock_acquire(self, lockpath, sizeof(lockpath));
    if (lockfd < 0) {
        fprintf(stderr, "fuse-agent: update skipped; another updater is active\n");
        return 0;
    }
    int rc = 0;
    char tmp[PATH_MAX] = {0}, neu[PATH_MAX] = {0};
    cJSON *root = NULL;
    char *body = NULL;
    snprintf(tmp, sizeof(tmp), "/tmp/fuse-manifest.%d", (int)getpid());
    if (http_get_file(g_url, "/agent/v1/manifest", tmp, 300000) != 0) goto out;
    FILE *f = fopen(tmp, "rb");
    if (!f) goto out;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    rewind(f);
    if (sz > 0 && sz < 200000) {
        body = malloc((size_t)sz + 1);
        if (body) {
            size_t got = fread(body, 1, (size_t)sz, f);
            if (got != (size_t)sz) {
                free(body);
                body = NULL;
            } else {
                body[sz] = 0;
            }
        }
    }
    fclose(f);
    unlink(tmp);
    tmp[0] = 0;
    if (!body) goto out;
    root = cJSON_Parse(body);
    free(body);
    body = NULL;
    if (!root) goto out;
    const char *ver = json_cstr(root, "version", "");
    if (!ver[0] || !ver_newer(ver, FUSE_AGENT_VERSION)) goto out;
    cJSON *bins = cJSON_GetObjectItemCaseSensitive(root, "binaries");
    cJSON *bin = bins ? cJSON_GetObjectItemCaseSensitive(bins, arch_sku()) : NULL;
    const char *path = json_cstr(bin, "path", "");
    const char *expect = json_cstr(bin, "sha256", "");
    if (!path[0] || !expect[0]) goto out;
    if (format_update_staging(neu, sizeof(neu), self) != 0) goto out;
    int sfd = mkstemp(neu);
    if (sfd < 0) goto out;
    close(sfd);
    if (http_get_file(g_url, path, neu, 128 * 1024 * 1024) != 0) {
        unlink(neu);
        goto out;
    }
    char got[72];
    if (sha256_file(neu, got, sizeof(got)) != 0 || strcmp(got, expect) != 0) {
        unlink(neu);
        goto out;
    }
    chmod(neu, 0755);
    if (rename(neu, self) != 0) {
        unlink(neu);
        goto out;
    }
    if (!allow_exec) {
        fprintf(stderr, "fuse-agent: updated to %s\n", ver);
        fprintf(stderr,
                "fuse-agent: restart the connector to activate "
                "(systemctl restart fuse-agent, or rerun it)\n");
        goto out;
    }
    fprintf(stderr, "fuse-agent: updated to %s, restarting\n", ver);
    {
        char *argv[] = {self, "run", "--config", g_config, NULL};
        execv(self, argv); /* O_CLOEXEC fds (incl. the lock) close here */
    }
    _exit(0);
out:
    if (root) cJSON_Delete(root);
    free(body);
    if (tmp[0]) unlink(tmp);
    close(lockfd);
    return rc;
}

#ifdef __linux__
static int default_route_up(void) {
    FILE *f = fopen("/proc/net/route", "r");
    if (f) {
        char line[256];
        int up = 0;
        if (fgets(line, sizeof(line), f)) {
            while (fgets(line, sizeof(line), f)) {
                char iface[64];
                unsigned dest = 1, gw = 0, flags = 0;
                if (sscanf(line, "%63s %x %x %x", iface, &dest, &gw, &flags) >= 4) {
                    if (dest == 0 && (flags & 0x0001)) {
                        up = 1;
                        break;
                    }
                }
            }
        }
        fclose(f);
        if (up) return 1;
    }
    f = fopen("/proc/net/ipv6_route", "r");
    if (!f) return 0;
    char line[256];
    int up = 0;
    while (fgets(line, sizeof(line), f)) {
        if (!strncmp(line, "00000000000000000000000000000000", 32)) {
            up = 1;
            break;
        }
    }
    fclose(f);
    return up;
}

static int open_netlink(void) {
    int fd = socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC | SOCK_NONBLOCK, NETLINK_ROUTE);
    if (fd < 0) return -1;
    struct sockaddr_nl sa;
    memset(&sa, 0, sizeof(sa));
    sa.nl_family = AF_NETLINK;
    sa.nl_groups = RTMGRP_IPV4_IFADDR | RTMGRP_IPV4_ROUTE | RTMGRP_IPV6_IFADDR | RTMGRP_IPV6_ROUTE |
                   RTMGRP_LINK;
    if (bind(fd, (struct sockaddr *)&sa, sizeof(sa)) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static void drain_netlink(int fd) {
    char buf[4096];
    while (recv(fd, buf, sizeof(buf), 0) > 0) {
    }
}

/* Optional libsystemd: PrepareForSleep + NetworkManager. No link-time dep. */
typedef struct sd_bus sd_bus;
typedef struct sd_bus_message sd_bus_message;
typedef struct sd_bus_slot sd_bus_slot;
typedef struct sd_bus_error sd_bus_error;
typedef int (*sd_bus_message_handler_t)(sd_bus_message *m, void *userdata, sd_bus_error *ret_error);

static int (*g_sd_bus_message_read)(sd_bus_message *, const char *, ...) = NULL;

static int on_sleep_signal(sd_bus_message *m, void *userdata, sd_bus_error *err) {
    (void)userdata;
    (void)err;
    int going = 0;
    if (!g_sd_bus_message_read || g_sd_bus_message_read(m, "b", &going) < 0) return 0;
    if (!going) request_reconnect(WAKE_RESUME);
    return 0;
}

static int on_nm_state(sd_bus_message *m, void *userdata, sd_bus_error *err) {
    (void)userdata;
    (void)err;
    unsigned state = 0;
    if (!g_sd_bus_message_read || g_sd_bus_message_read(m, "u", &state) < 0) return 0;
    if (state == 70) request_reconnect(WAKE_NETWORK); /* NM_STATE_CONNECTED_GLOBAL */
    return 0;
}

static void *watch_thread(void *arg) {
    (void)arg;
    int nl = open_netlink();
    int had_route = default_route_up();

    sd_bus *bus = NULL;
    int bus_fd = -1;
    void *h = dlopen("libsystemd.so.0", RTLD_LAZY | RTLD_LOCAL);
    int (*sd_bus_open_system)(sd_bus **) = NULL;
    sd_bus *(*sd_bus_unref)(sd_bus *) = NULL;
    int (*sd_bus_match_signal)(sd_bus *, sd_bus_slot **, const char *, const char *, const char *,
                               const char *, sd_bus_message_handler_t, void *) = NULL;
    int (*sd_bus_process)(sd_bus *, sd_bus_message **) = NULL;
    int (*sd_bus_get_fd)(sd_bus *) = NULL;
    int (*sd_bus_flush)(sd_bus *) = NULL;
    if (h) {
        sd_bus_open_system = dlsym(h, "sd_bus_open_system");
        sd_bus_unref = dlsym(h, "sd_bus_unref");
        sd_bus_match_signal = dlsym(h, "sd_bus_match_signal");
        g_sd_bus_message_read = dlsym(h, "sd_bus_message_read");
        sd_bus_process = dlsym(h, "sd_bus_process");
        sd_bus_get_fd = dlsym(h, "sd_bus_get_fd");
        sd_bus_flush = dlsym(h, "sd_bus_flush");
        if (sd_bus_open_system && sd_bus_match_signal && sd_bus_open_system(&bus) >= 0) {
            sd_bus_match_signal(bus, NULL, "org.freedesktop.login1", "/org/freedesktop/login1",
                                "org.freedesktop.login1.Manager", "PrepareForSleep", on_sleep_signal,
                                NULL);
            sd_bus_match_signal(bus, NULL, "org.freedesktop.NetworkManager",
                                "/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager",
                                "StateChanged", on_nm_state, NULL);
            if (sd_bus_flush) sd_bus_flush(bus);
            if (sd_bus_get_fd) bus_fd = sd_bus_get_fd(bus);
        } else {
            bus = NULL;
        }
    }

    while (1) {
        struct pollfd p[3];
        int n = 0;
        int nl_i = -1, bus_i = -1, stop_i = -1;
        if (nl >= 0) {
            nl_i = n;
            p[n].fd = nl;
            p[n].events = POLLIN;
            p[n].revents = 0;
            n++;
        }
        if (bus_fd >= 0) {
            bus_i = n;
            p[n].fd = bus_fd;
            p[n].events = POLLIN;
            p[n].revents = 0;
            n++;
        }
        if (g_watch_stop_rd >= 0) {
            stop_i = n;
            p[n].fd = g_watch_stop_rd;
            p[n].events = POLLIN;
            p[n].revents = 0;
            n++;
        }
        int pr = poll(p, n, 1000);
        (void)pr;
        if (stop_i >= 0 && (p[stop_i].revents & POLLIN)) break;
        if (nl_i >= 0 && (p[nl_i].revents & POLLIN)) drain_netlink(nl);
        if (bus_i >= 0 && (p[bus_i].revents & POLLIN) && sd_bus_process) sd_bus_process(bus, NULL);
        int up = default_route_up();
        if (up && !had_route) request_reconnect(WAKE_NETWORK);
        had_route = up;
    }
    if (nl >= 0) close(nl);
    if (bus && sd_bus_unref) sd_bus_unref(bus);
    if (h) dlclose(h);
    return NULL;
}
#else
static void *watch_thread(void *arg) {
    (void)arg;
    int64_t last_boot = now_boot_ms();
    int64_t last_mono = now_mono_ms();
    while (1) {
        /* Sleep on the watch stop pipe only: the main wake pipe belongs to
         * the session-loop reader, and no shared stop flag is read here. */
        struct pollfd p = {g_watch_stop_rd, POLLIN, 0};
        int pr = poll(&p, g_watch_stop_rd >= 0 ? 1 : 0, 1000);
        if (pr > 0 && (p.revents & POLLIN)) break;
        int64_t boot = now_boot_ms();
        int64_t mono = now_mono_ms();
        if ((boot - last_boot) - (mono - last_mono) >= SUSPEND_GAP_MS) request_reconnect(WAKE_RESUME);
        last_boot = boot;
        last_mono = mono;
    }
    return NULL;
}
#endif

static void start_watch_thread(void) {
    if (g_watch_started) return;
    int p[2], q[2], r[2];
    if (pipe2(p, O_CLOEXEC | O_NONBLOCK) == 0) {
        g_wake_rd = p[0];
        g_wake_wr = p[1];
    }
    if (pipe2(q, O_CLOEXEC | O_NONBLOCK) == 0) {
        g_main_stop_rd = q[0];
        g_main_stop_wr = q[1];
    }
    if (pipe2(r, O_CLOEXEC) == 0) {
        g_watch_stop_rd = r[0];
        g_watch_stop_wr = r[1];
    }
    pthread_t th;
    if (pthread_create(&th, NULL, watch_thread, NULL) == 0) {
        pthread_detach(th);
        g_watch_started = 1;
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

/* No queued results and no running/queued jobs: safe to execv() into an
 * update without orphaning in-flight work. */
static int agent_idle(void) {
    /* Order matters: a worker enqueues its result (g_out_mu) strictly before
     * dropping g_busy (g_job_mu). Sampling jobs first, then outputs, cannot
     * miss work: jobs==0 implies every completed job is already counted in
     * g_out_n. The previous outputs-first order could observe out_n==0, then
     * a completion enqueued, then jobs==0, and report idle while a result was
     * still queued - and maybe_update's execv would discard it. */
    pthread_mutex_lock(&g_job_mu);
    int jobs = g_qcount + g_busy;
    pthread_mutex_unlock(&g_job_mu);
    pthread_mutex_lock(&g_out_mu);
    int out_n = g_out_n;
    pthread_mutex_unlock(&g_out_mu);
    return out_n == 0 && jobs == 0;
}

static int session_loop(void) {
    struct url base, u;
    if (parse_url(g_url, &base) != 0) die("invalid url");
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
    int64_t next_update_ms = now_mono_ms() + 60 * 1000;
    start_watch_thread();
    while (!g_stop_main) {
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
            fprintf(stderr, "fuse-agent: %s\n", err);
            cJSON_Delete(reply);
            tls_close(&t);
            if (strstr(err, "invalid") || strstr(err, "expired") || strstr(err, "token")) {
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
            strncpy(g_token, tok, sizeof(g_token) - 1);
            g_code[0] = 0;
            if (g_config[0]) save_config(g_config);
        }
        fprintf(stderr, "fuse-agent: connected as %s\n", json_cstr(reply, "alias", "machine"));
        cJSON_Delete(reply);
        fail_index = 0;
        auth_backoff = 0;
        attempts = 0;
        ever_connected = 1;
        int replaced = 0;
        const char *drop_reason = NULL;
        struct conn_watch watch;
        conn_watch_init(&watch, now_mono_ms(), now_boot_ms());
        set_live_fd(t.fd);
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
                    next_update_ms = now_mono_ms() + UPDATE_EVERY_MS;
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
                if (jid[0]) job_reserve(jid);
                enqueue_job(o); /* ownership moves to the worker */
                o = NULL;
            } else if (strcmp(tt, "cancel") == 0) {
                 char cid[16];
                 if (json_str(o, "id", cid, sizeof(cid)) == 0) {
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
                fprintf(stderr, "fuse-agent: disconnected (%s); reconnecting\n",
                        drop_reason ? drop_reason : "socket closed");
                fail_index = 0;
                attempts = 0;
            }
        }
    }
    return 0;
}

static int write_unit(int system_wide, const char *bin, const char *cfg) {
    char path[PATH_MAX];
    if (system_wide) snprintf(path, sizeof(path), "/etc/systemd/system/fuse-agent.service");
    else {
        const char *home = getenv("HOME");
        if (!home) return -1;
        snprintf(path, sizeof(path), "%s/.config/systemd/user/fuse-agent.service", home);
        char dir[PATH_MAX];
        snprintf(dir, sizeof(dir), "%s/.config/systemd/user", home);
        mkdir(dir, 0755);
    }
    FILE *f = fopen(path, "w");
    if (!f) return -1;
    if (system_wide) {
        fprintf(f,
                "[Unit]\n"
                "Description=Fuse connector\n"
                "After=network-online.target\n"
                "Wants=network-online.target\n"
                "\n"
                "[Service]\n"
                "Type=simple\n"
                "ExecStart=\"%s\" run --config \"%s\"\n"
                "Restart=always\n"
                "RestartSec=3\n"
                "# NoNewPrivileges blocks setuid/file-capability helpers (sudo,\n"
                "# setuid ping) inside agent jobs, by design. Do not remove lightly.\n"
                "NoNewPrivileges=true\n"
                "\n"
                "[Install]\n"
                "WantedBy=multi-user.target\n",
                bin, cfg);
    } else {
        /* User systemd has no network-online.target; After= on it is a no-op.
           Resume reconnect is in-process (logind, default route, ping timeout). */
        fprintf(f,
                "[Unit]\n"
                "Description=Fuse connector\n"
                "# User systemd has no network-online.target. After sleep the process\n"
                "# reconnects itself (logind PrepareForSleep, default-route watch, ping).\n"
                "# Optional root hook: /usr/lib/systemd/system-sleep/fuse-agent (SIGUSR1).\n"
                "\n"
                "[Service]\n"
                "Type=simple\n"
                "ExecStart=\"%s\" run --config \"%s\"\n"
                "Restart=always\n"
                "RestartSec=3\n"
                "# NoNewPrivileges blocks setuid/file-capability helpers (sudo,\n"
                "# setuid ping) inside agent jobs, by design. Do not remove lightly.\n"
                "NoNewPrivileges=true\n"
                "\n"
                "[Install]\n"
                "WantedBy=default.target\n",
                bin, cfg);
    }
    fclose(f);
    return 0;
}

static int write_sleep_hook(void) {
    const char *path = "/usr/lib/systemd/system-sleep/fuse-agent";
    FILE *f = fopen(path, "w");
    if (!f) return -1;
    fputs("#!/bin/sh\n"
          "# Poke fuse-agent after resume so half-open sockets are dropped.\n"
          "# In-process reconnect (logind / default route / ping) is the real fix;\n"
          "# this hook is belt-and-suspenders. SIGUSR1 keeps the same PID.\n"
          "[ \"$1\" = post ] || exit 0\n"
          "systemctl kill -s USR1 fuse-agent.service >/dev/null 2>&1 || true\n"
          "if command -v loginctl >/dev/null 2>&1; then\n"
          "  loginctl list-users --no-legend 2>/dev/null | while read -r uid user rest; do\n"
          "    [ -n \"$uid\" ] || continue\n"
          "    [ -S \"/run/user/$uid/systemd/private\" ] || continue\n"
          "    systemctl --user -M \"${user}@\" kill -s USR1 fuse-agent.service >/dev/null 2>&1 || true\n"
          "  done\n"
          "fi\n"
          "exit 0\n",
          f);
    fclose(f);
    chmod(path, 0755);
    return 0;
}

static int copy_self(const char *dest) {
    char self[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", self, sizeof(self) - 1);
    if (n < 0) return -1;
    self[n] = 0;
    FILE *in = fopen(self, "rb");
    FILE *out = fopen(dest, "wb");
    if (!in || !out) {
        if (in) fclose(in);
        if (out) fclose(out);
        return -1;
    }
    char buf[8192];
    size_t k;
    while ((k = fread(buf, 1, sizeof(buf), in)) > 0) {
        if (fwrite(buf, 1, k, out) != k) {
            fclose(in);
            fclose(out);
            return -1;
        }
    }
    fclose(in);
    fclose(out);
    chmod(dest, 0755);
    return 0;
}

/* One-shot pair: connect, hello, save token, return. */
static int pair_once(void) {
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
        die("%s", json_cstr(reply, "error", "pairing failed"));
    }
    const char *tok = json_cstr(reply, "token", "");
    if (tok[0]) strncpy(g_token, tok, sizeof(g_token) - 1);
    g_code[0] = 0;
    fprintf(stderr, "fuse-agent: paired as %s\n", json_cstr(reply, "alias", "machine"));
    cJSON_Delete(reply);
    tls_close(&t);
    return 0;
}

static int install_service(void) {
    int root = (geteuid() == 0);
    if (root) {
        fprintf(stderr,
                "fuse-agent: installing as root; the paired Fuse account will be able "
                "to run commands as root on this machine. Prefer an unprivileged user "
                "if the workload allows it.\n");
    }
    char bindir[PATH_MAX], bin[PATH_MAX];
    if (root) {
        mkdir("/usr/local/bin", 0755);
        mkdir("/etc/fuse-agent", 0700);
        snprintf(bin, sizeof(bin), "%s", "/usr/local/bin/fuse-agent");
    } else {
        const char *home = getenv("HOME");
        if (!home) die("HOME is not set");
        if (snprintf(bindir, sizeof(bindir), "%s/.local/bin", home) >= (int)sizeof(bindir)) die("path too long");
        mkdir(bindir, 0755);
        char cfgdir[PATH_MAX];
        if (snprintf(cfgdir, sizeof(cfgdir), "%s/.config/fuse-agent", home) >= (int)sizeof(cfgdir)) die("path too long");
        mkdir(cfgdir, 0700);
        if (snprintf(bin, sizeof(bin), "%s/fuse-agent", bindir) >= (int)sizeof(bin)) die("path too long");
    }
    if (copy_self(bin) != 0) die("could not install binary to %s", bin);
    default_config_path(g_config, sizeof(g_config), root);
    g_auto_update = 1;
    if (g_code[0] || !g_token[0]) pair_once();
    if (save_config(g_config) != 0) die("could not write %s", g_config);
    if (write_unit(root, bin, g_config) != 0) die("could not write systemd unit");
    if (root && write_sleep_hook() != 0)
        fprintf(stderr, "fuse-agent: could not write /usr/lib/systemd/system-sleep/fuse-agent\n");
    char cmd[640];
    if (root) {
        snprintf(cmd, sizeof(cmd), "systemctl daemon-reload && systemctl enable --now fuse-agent");
    } else {
        snprintf(cmd, sizeof(cmd),
                 "systemctl --user daemon-reload && systemctl --user enable --now fuse-agent; "
                 "command -v loginctl >/dev/null && loginctl enable-linger \"$(id -un)\" >/dev/null 2>&1 || true");
    }
    int st = system(cmd);
    if (st != 0) die("systemd enable failed (is systemd running?)");
    fprintf(stderr, "fuse-agent: installed and started (%s)\n", root ? "system" : "user");
    return 0;
}

static int uninstall_service(void) {
    int root = (geteuid() == 0);
    char cfg[PATH_MAX];
    default_config_path(cfg, sizeof(cfg), root);
    if (root) {
        (void)!system("systemctl disable --now fuse-agent >/dev/null 2>&1");
        (void)!system("systemctl daemon-reload >/dev/null 2>&1");
        unlink("/etc/systemd/system/fuse-agent.service");
        unlink("/usr/local/bin/fuse-agent");
        unlink("/usr/lib/systemd/system-sleep/fuse-agent");
    } else {
        (void)!system("systemctl --user disable --now fuse-agent >/dev/null 2>&1");
        (void)!system("systemctl --user daemon-reload >/dev/null 2>&1");
        const char *home = getenv("HOME");
        if (home) {
            char p[PATH_MAX];
            if (snprintf(p, sizeof(p), "%s/.config/systemd/user/fuse-agent.service", home) < (int)sizeof(p))
                unlink(p);
            if (snprintf(p, sizeof(p), "%s/.local/bin/fuse-agent", home) < (int)sizeof(p)) unlink(p);
        }
    }
    /* Remove the credential last so the service is gone before the token is. */
    unlink(cfg);
    if (root) rmdir("/etc/fuse-agent");
    else {
        const char *home = getenv("HOME");
        if (home) {
            char p[PATH_MAX];
            if (snprintf(p, sizeof(p), "%s/.config/fuse-agent", home) < (int)sizeof(p)) rmdir(p);
        }
    }
    fprintf(stderr,
            "fuse-agent: uninstalled (service, binary and credentials removed)\n"
            "fuse-agent: also remove this machine in the Fuse dashboard to revoke its token\n");
    return 0;
}

static void usage(void) {
    fprintf(stderr,
            "fuse-agent %s\n"
            "  run     [--url URL] [--code CODE] [--token TOK] [--config FILE]\n"
            "  install [--url URL] [--code CODE]\n"
            "  uninstall\n"
            "  update\n",
            FUSE_AGENT_VERSION);
}

int main(int argc, char **argv) {
    srand((unsigned)(time(NULL) ^ getpid()));
    setvbuf(stderr, NULL, _IOLBF, 0);
    SSL_library_init();
    SSL_load_error_strings();
    const char *cmd = "run";
    int i = 1;
    if (i < argc && argv[i][0] != '-') cmd = argv[i++];
    for (; i < argc; i++) {
        if (strcmp(argv[i], "--url") == 0 && i + 1 < argc) strncpy(g_url, argv[++i], sizeof(g_url) - 1);
        else if (strcmp(argv[i], "--code") == 0 && i + 1 < argc) strncpy(g_code, argv[++i], sizeof(g_code) - 1);
        else if (strcmp(argv[i], "--token") == 0 && i + 1 < argc) strncpy(g_token, argv[++i], sizeof(g_token) - 1);
        else if (strcmp(argv[i], "--config") == 0 && i + 1 < argc) strncpy(g_config, argv[++i], sizeof(g_config) - 1);
        else if (strcmp(argv[i], "--foreground") == 0 || strcmp(argv[i], "--once") == 0) { /* run stays foreground */ }
        else if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            usage();
            return 0;
        } else if (strncmp(argv[i], "fuse_", 5) == 0) {
            strncpy(g_code, argv[i], sizeof(g_code) - 1);
        } else {
            die("unknown argument: %s", argv[i]);
        }
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_sig);
    signal(SIGINT, on_sig);
    signal(SIGUSR1, on_usr1);
    if (!g_config[0]) default_config_path(g_config, sizeof(g_config), geteuid() == 0);
    if (strcmp(cmd, "install") == 0) {
        /* Load an existing config so a re-install over a paired machine
         * reuses its token instead of demanding a fresh pairing code. */
        load_config(g_config);
        if (!g_url[0]) die("install requires --url");
        return install_service();
    }
    if (strcmp(cmd, "uninstall") == 0) return uninstall_service();
    if (g_config[0]) load_config(g_config);
    if (strcmp(cmd, "update") == 0) {
        g_auto_update = 1;
        /* allow_exec=0: a manual update stages the verified binary and never
         * execs a second daemon or disturbs the running one. */
        return maybe_update(0);
    }
    if (strcmp(cmd, "run") != 0) {
        usage();
        return 1;
    }
    if (!g_url[0]) die("missing --url or config url");
    if (!g_token[0] && !g_code[0]) die("missing --code/--token");
    return session_loop();
}
