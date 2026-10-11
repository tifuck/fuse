/* Fuse connector: TLS + WebSocket transport (URL parse, connect, framing). */
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/types.h>

#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/ssl.h>

#include "agent.h"
#include "lifecycle.h"
#include "net.h"
#include "reconnect.h"

int parse_url(const char *raw, struct url *u) {
    memset(u, 0, sizeof(*u));
    if (strncmp(raw, "https://", 8) != 0) return -1;
    const char *p = raw + 8;
    const char *slash = strchr(p, '/');
    size_t hostlen;
    u->port = 443;
    if (*p == '[') {
        /* IPv6 literal: host ends at ']'; a colon must follow it. */
        const char *close = strchr(p, ']');
        if (!close) return -1;
        hostlen = (size_t)(close - (p + 1));
        if (hostlen == 0 || hostlen >= sizeof(u->host)) return -1;
        memcpy(u->host, p + 1, hostlen);
        u->host[hostlen] = 0;
        if (close[1] == ':') {
            u->port = atoi(close + 2);
            if (u->port <= 0 || u->port > 65535) return -1;
        } else if (close[1] && close[1] != '/') {
            return -1;
        }
        slash = strchr(close, '/');
    } else {
        const char *colon = strchr(p, ':');
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
    }
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

int tls_connect(struct tls *t, const struct url *u) {
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
    /* SSL_set1_host failing would leave the peer's hostname unchecked:
     * refuse the connection rather than accept any trusted certificate. */
    if (!ssl || SSL_set_fd(ssl, fd) != 1 || SSL_set_tlsext_host_name(ssl, u->host) != 1 ||
        SSL_set1_host(ssl, u->host) != 1) {
        if (ssl) SSL_free(ssl);
        SSL_CTX_free(ctx);
        close(fd);
        return -1;
    }
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

void tls_close(struct tls *t) {
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

int tls_write_all(struct tls *t, const void *buf, size_t n) {
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

int tls_read_some(struct tls *t, void *buf, size_t n) {
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

int b64(const unsigned char *in, int inlen, char *out, int outcap) {
    if (outcap < 4 * ((inlen + 2) / 3) + 1) return -1;
    if (EVP_EncodeBlock((unsigned char *)out, in, inlen) < 0) return -1;
    return 0;
}

int ws_handshake(struct tls *t, const struct url *u) {
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
    /* Keep any bytes the server pipelined after the handshake response;
     * more than the stash would be silently lost and desync framing. */
    if (got > hdr_end) {
        size_t extra = got - hdr_end;
        if (extra > sizeof(t->pb)) return -1;
        memcpy(t->pb, buf + hdr_end, extra);
        t->pb_len = extra;
        t->pb_off = 0;
    }
    return 0;
}

/* Build the frame header: FIN|opcode, masked flag, 7/16/64-bit length, then
 * the 4-byte mask at hdr+hlen. Returns the header length. The 64-bit length
 * occupies hdr[2..9] so the mask lands at hdr[10..13]. */
size_t ws_header(int opcode, const unsigned char *mask, size_t n, unsigned char *hdr) {
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
        memset(hdr + 2, 0, 4);
        hdr[6] = (unsigned char)((n >> 24) & 0xff);
        hdr[7] = (unsigned char)((n >> 16) & 0xff);
        hdr[8] = (unsigned char)((n >> 8) & 0xff);
        hdr[9] = (unsigned char)(n & 0xff);
        hlen = 10;
    }
    memcpy(hdr + hlen, mask, 4);
    return hlen + 4;
}

int ws_send(struct tls *t, int opcode, const unsigned char *data, size_t n) {
    unsigned char hdr[14];
    unsigned char mask[4];
    if (RAND_bytes(mask, 4) != 1) return -1;
    size_t hlen = ws_header(opcode, mask, n, hdr);
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

int ws_send_text(struct tls *t, const char *s) {
    return ws_send(t, 1, (const unsigned char *)s, strlen(s));
}

static int ssl_read_n(struct tls *t, unsigned char *buf, size_t n) {
    size_t got = 0;
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
            /* Fresh budget per attempt. One deadline for the whole frame let
             * a slow but progressing read of a large frame expire. */
            if (wait_ssl(t, err, IO_TIMEOUT_MS) <= 0) return -1;
            continue;
        }
        return -1;
    }
    return 0;
}

char *ws_read_text(struct tls *t, int *opcode_out, size_t *n_out) {
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
        if (opcode == 9 || opcode == 10) {
            if (!acc && opcode_out) {
                /* Nothing in progress: hand the control frame back so the
                 * session loop polls again. Reading on here would wait for a
                 * next frame that may never come and then drop the session. */
                if (n_out) *n_out = (size_t)n;
                *opcode_out = opcode;
                return (char *)payload;
            }
            /* Mid-fragment (or the hello read): answer inline so a partial
             * message survives an interleaved control frame (RFC 6455 5.5.1). */
            int prc = (opcode == 9) ? ws_send(t, 10, payload, (size_t)n) : 0;
            free(payload);
            if (prc != 0) {
                free(acc);
                return NULL;
            }
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
        if (acc_len + (size_t)n > WS_MAX) {
            /* Legal-size continuation frames would otherwise grow this
             * buffer without bound; cap the accumulated message too. */
            free(payload);
            free(acc);
            return NULL;
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
