/* Fuse connector: self-update (manifest fetch, sha256 verify, flock, staging). */
#include <fcntl.h>
#include <pthread.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>

#include <openssl/evp.h>

#include "agent.h"
#include "config.h"
#include "net.h"
#include "reconnect.h"
#include "update.h"

/* Buffered response reader. One deadline for the whole response: a read
 * alone gives each stalled read a fresh timeout, so a peer trickling bytes
 * could hold the update lock forever. */
struct rbuf {
    int (*rd)(void *ctx, char *buf, size_t n);
    void *ctx;
    char b[8192];
    size_t s, e;
    int64_t deadline;
};

static int rb_fill(struct rbuf *r) {
    if (r->s < r->e) return 1;
    if (now_mono_ms() >= r->deadline) return -1;
    int k = r->rd(r->ctx, r->b, sizeof(r->b));
    if (k <= 0) return 0; /* closed (or failed): callers check lengths */
    r->s = 0;
    r->e = (size_t)k;
    return 1;
}

/* One header or chunk-size line, CRLF stripped. */
static int rb_line(struct rbuf *r, char *line, size_t cap) {
    size_t n = 0;
    for (;;) {
        if (rb_fill(r) != 1) return -1;
        char c = r->b[r->s++];
        if (c == '\n') break;
        if (n + 1 >= cap) return -1;
        line[n++] = c;
    }
    if (n && line[n - 1] == '\r') n--;
    line[n] = 0;
    return 0;
}

/* Copy up to want bytes (or until close) into f; returns the count. */
static size_t rb_copy(struct rbuf *r, FILE *f, size_t want, int *err) {
    size_t done = 0;
    while (done < want) {
        int fr = rb_fill(r);
        if (fr == -1) *err = 1;
        if (fr != 1) break;
        size_t take = r->e - r->s;
        if (take > want - done) take = want - done;
        if (fwrite(r->b + r->s, 1, take, f) != take) {
            *err = 1;
            break;
        }
        r->s += take;
        done += take;
    }
    return done;
}

int http_read_response(int (*rd)(void *ctx, char *buf, size_t n), void *ctx, FILE *f, size_t max_bytes) {
    struct rbuf *rb = malloc(sizeof(*rb));
    if (!rb) return -1;
    *rb = (struct rbuf){.rd = rd, .ctx = ctx, .deadline = now_mono_ms() + 5 * 60 * 1000};
    char line[1024];
    int chunked = 0, bad = 0;
    long long clen = -1;
    if (rb_line(rb, line, sizeof(line)) != 0 ||
        (strncmp(line, "HTTP/1.1 200", 12) != 0 && strncmp(line, "HTTP/1.0 200", 12) != 0))
        bad = 1;
    while (!bad) {
        if (rb_line(rb, line, sizeof(line)) != 0) bad = 1;
        else if (!line[0]) break;
        else if (strncasecmp(line, "transfer-encoding:", 18) == 0 && strcasestr(line + 18, "chunked")) chunked = 1;
        else if (strncasecmp(line, "content-length:", 15) == 0) {
            clen = strtoll(line + 15, NULL, 10);
            if (clen < 0) bad = 1; /* -1 is the no-length sentinel: never read-to-close on a bad header */
        }
    }
    if (clen > (long long)max_bytes) bad = 1;
    size_t total = 0;
    if (!bad && chunked) {
        /* A proxy (nginx with gzip, for one) may chunk the response. */
        for (;;) {
            char *end;
            unsigned long long sz;
            if (rb_line(rb, line, sizeof(line)) != 0 || (sz = strtoull(line, &end, 16), end == line) ||
                sz > max_bytes - total) {
                bad = 1;
                break;
            }
            if (sz == 0) break; /* trailers are ignored */
            if (rb_copy(rb, f, (size_t)sz, &bad) != sz || rb_line(rb, line, sizeof(line)) != 0 || line[0]) {
                bad = 1;
                break;
            }
            total += (size_t)sz;
        }
    } else if (!bad) {
        total = rb_copy(rb, f, clen >= 0 ? (size_t)clen : max_bytes + 1, &bad);
        if (total > max_bytes || (clen >= 0 && total != (size_t)clen)) bad = 1;
    }
    free(rb);
    return bad || ferror(f) ? -1 : 0;
}

static int tls_reader(void *ctx, char *buf, size_t n) {
    return tls_read_some(ctx, buf, n);
}

static int http_get_file(const char *base, const char *path, int fd, size_t max_bytes) {
    struct url u;
    if (parse_url(base, &u) != 0 || url_join_path(&u, path) != 0) return -1;
    struct tls t;
    if (tls_connect(&t, &u) != 0) return -1;
    char req[800], host[300];
    url_host_header(&u, host, sizeof(host));
    int n = snprintf(req, sizeof(req), "GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n", u.path, host);
    if (n < 0 || (size_t)n >= sizeof(req) || tls_write_all(&t, req, (size_t)n) != 0) {
        tls_close(&t);
        return -1;
    }
    /* Write through a dup so the caller always keeps sole ownership of fd and
     * closes it exactly once, on success and failure alike. */
    int wfd = dup(fd);
    FILE *f = wfd >= 0 ? fdopen(wfd, "wb") : NULL;
    if (!f) {
        if (wfd >= 0) close(wfd);
        tls_close(&t);
        return -1;
    }
    int rc = http_read_response(tls_reader, &t, f, max_bytes);
    if (fflush(f) != 0) rc = -1;
    if (rc == 0) fsync(fd);
    fclose(f);
    tls_close(&t);
    return rc;
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

/* The served binary is already the one running: a manifest whose version
 * outruns its binaries (a SKU left unbuilt, a rollback) would otherwise
 * download and re-exec the same file on every check. */
int update_is_self(const char *self, const char *expect_sha) {
    char mine[72];
    return sha256_file(self, mine, sizeof(mine)) == 0 && strcmp(mine, expect_sha) == 0;
}

static int ver_parse(const char *s, int v[3]) {
    int n = 0;
    while (n < 3) {
        char *end;
        long x = strtol(s, &end, 10);
        if (end == s || x < 0 || x > 1000000) return -1;
        v[n++] = (int)x;
        if (*end != '.') {
            /* Reject trailing junk ("1.2x", "1.2.3.4") instead of silently
             * truncating it into a different version. */
            if (*end) return -1;
            break;
        }
        s = end + 1;
    }
    while (n < 3) v[n++] = 0;
    return 0;
}

/* Strictly newer only: never downgrade on an equal or lower remote version. */
int ver_newer(const char *cand, const char *cur) {
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
int update_lock_acquire(const char *self, char *lockpath, size_t cap) {
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
int format_update_staging(char *dst, size_t cap, const char *self) {
    size_t n = strlen(self);
    if (n + sizeof(".new.XXXXXX") > cap) return -1;
    memcpy(dst, self, n);
    memcpy(dst + n, ".new.XXXXXX", sizeof(".new.XXXXXX"));
    return 0;
}

/* mode: UPDATE_STAGE (daemon, background thread) fetches, verifies and
 * installs the new build, then marks it pending; UPDATE_EXEC (session
 * thread, idle-gated) only execv()s into a pending build - same PID, single
 * daemon - and never touches the network, so the session never stalls on a
 * download. A manual `fuse-agent update` (UPDATE_MANUAL) runs in its own
 * process and must never become a second live daemon (two instances on one
 * token replace each other forever), so it stages the verified binary and
 * leaves restart ownership to the operator or systemd. */
/* A failed update says why and returns non-zero ("already current" is 0):
 * a manual `fuse-agent update` exited 0 silently on every failure. */
#define UPDATE_FAIL(msg)                                              \
    do {                                                              \
        fprintf(stderr, "fuse-agent: update failed: %s\n", (msg));   \
        rc = 1;                                                       \
        goto out;                                                     \
    } while (0)
#if defined(__aarch64__)
#define FUSE_ARCH_LABEL "linux-arm64"
#else
#define FUSE_ARCH_LABEL "linux-amd64"
#endif

/* Set by the session: non-zero when work is waiting (frames unread on the
 * socket, jobs queued). execv would drop that work, so the restart waits
 * for the next idle check instead. */
int (*update_exec_veto)(void) = NULL;
/* A verified binary is installed over self (by the background stager). */
static int s_exec_pending = 0;
static int s_bg_running = 0;
/* Path the build was installed to: once renamed over, /proc/self/exe
 * reads "... (deleted)" and cannot be exec'd. */
static char s_staged_self[PATH_MAX];

int update_exec_pending(void) { return __atomic_load_n(&s_exec_pending, __ATOMIC_ACQUIRE); }

static void exec_self(const char *self) {
    char *argv[] = {(char *)self, "run", "--config", g_config, NULL};
    execv(self, argv); /* O_CLOEXEC fds (incl. the lock) close here */
}

int maybe_update(int mode) {
    if (!g_auto_update) return 0;
    char lockpath[PATH_MAX];
    if (mode == UPDATE_EXEC) {
        /* No network here: the session thread only commits a staged binary. */
        if (!update_exec_pending()) return 0;
        int lockfd = update_lock_acquire(s_staged_self, lockpath, sizeof(lockpath));
        if (lockfd < 0) return 1;
        if (!(update_exec_veto && update_exec_veto())) {
            fprintf(stderr, "fuse-agent: restarting into the staged update\n");
            exec_self(s_staged_self);
            _exit(0);
        }
        close(lockfd);
        return 0;
    }
    if (!g_url[0]) {
        fprintf(stderr, "fuse-agent: update failed: no server url configured\n");
        return 1;
    }
    char self[PATH_MAX];
    ssize_t k = readlink("/proc/self/exe", self, sizeof(self) - 1);
    if (k < 0) {
        fprintf(stderr, "fuse-agent: update failed: cannot locate the running binary: %s\n", strerror(errno));
        return 1;
    }
    self[k] = 0;
    int lockfd = update_lock_acquire(self, lockpath, sizeof(lockpath));
    if (lockfd < 0) {
        fprintf(stderr, "fuse-agent: update skipped; another updater is active\n");
        return 1;
    }
    int rc = 0, staged = 0;
    char tmp[PATH_MAX] = {0}, neu[PATH_MAX] = {0};
    cJSON *root = NULL;
    char *body = NULL;
    /* mkstemp, not a predictable path + fopen: a symlink planted in /tmp by
     * another local user must never be truncated by an updater running as
     * root. */
    snprintf(tmp, sizeof(tmp), "/tmp/fuse-manifest.XXXXXX");
    int mfd = mkstemp(tmp);
    if (mfd < 0) UPDATE_FAIL("could not create a temp file");
    int fetched = http_get_file(g_url, "/agent/v1/manifest", mfd, 300000);
    close(mfd);
    mfd = -1;
    if (fetched != 0) UPDATE_FAIL("could not download the manifest");
    /* /tmp is sticky and the file is ours 0600, so reopening by name cannot
     * be swapped by another user. */
    mfd = open(tmp, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (mfd < 0) UPDATE_FAIL("could not reopen the manifest");
    struct stat st;
    if (fstat(mfd, &st) != 0 || st.st_size <= 0 || st.st_size >= 200000) UPDATE_FAIL("manifest has an invalid size");
    body = malloc((size_t)st.st_size + 1);
    if (body) {
        size_t want = (size_t)st.st_size, got = 0;
        ssize_t rd;
        while (got < want && (rd = read(mfd, body + got, want - got)) > 0) got += (size_t)rd;
        if (got != want) {
            free(body);
            body = NULL;
        } else {
            body[want] = 0;
        }
    }
    close(mfd);
    mfd = -1;
    unlink(tmp);
    tmp[0] = 0;
    if (!body) UPDATE_FAIL("could not read the manifest");
    root = cJSON_Parse(body);
    free(body);
    body = NULL;
    if (!root) UPDATE_FAIL("manifest is not valid JSON");
    const char *ver = json_cstr(root, "version", "");
    if (!ver[0]) UPDATE_FAIL("manifest has no version");
    if (!ver_newer(ver, FUSE_AGENT_VERSION)) {
        if (mode == UPDATE_MANUAL) fprintf(stderr, "fuse-agent: already up to date (%s)\n", FUSE_AGENT_VERSION);
        goto out;
    }
    cJSON *bins = cJSON_GetObjectItemCaseSensitive(root, "binaries");
    cJSON *bin = bins ? cJSON_GetObjectItemCaseSensitive(bins, arch_sku()) : NULL;
    const char *path = json_cstr(bin, "path", "");
    const char *expect = json_cstr(bin, "sha256", "");
    /* The path goes into a raw HTTP request line: spaces or CR/LF in a
     * crafted manifest would inject extra headers. Require a clean path. */
    if (path[0] != '/' || !expect[0] || strpbrk(path, " \t\r\n\"?#")) UPDATE_FAIL("manifest has no usable binary for " FUSE_ARCH_LABEL);
    if (update_is_self(self, expect)) goto out;
    if (format_update_staging(neu, sizeof(neu), self) != 0) UPDATE_FAIL("install path is too long");
    int sfd = mkstemp(neu);
    if (sfd < 0) UPDATE_FAIL("could not create the staging file next to the binary (is the directory writable?)");
    int got_bin = http_get_file(g_url, path, sfd, 128 * 1024 * 1024);
    close(sfd);
    if (got_bin != 0) {
        unlink(neu);
        UPDATE_FAIL("could not download the new binary");
    }
    char got[72];
    if (sha256_file(neu, got, sizeof(got)) != 0 || strcmp(got, expect) != 0) {
        unlink(neu);
        UPDATE_FAIL("downloaded binary failed its sha256 check");
    }
    chmod(neu, 0755);
    if (rename(neu, self) != 0) {
        unlink(neu);
        UPDATE_FAIL("could not replace the installed binary");
    }
    if (mode == UPDATE_MANUAL) {
        fprintf(stderr, "fuse-agent: updated to %s\n", ver);
        fprintf(stderr,
                "fuse-agent: restart the connector to activate "
                "(systemctl restart fuse-agent, or rerun it)\n");
        goto out;
    }
    fprintf(stderr, "fuse-agent: updated to %s; restarting when idle\n", ver);
    memcpy(s_staged_self, self, sizeof(s_staged_self));
    staged = 1;
out:
    if (mfd >= 0) close(mfd);
    if (root) cJSON_Delete(root);
    free(body);
    if (tmp[0]) unlink(tmp);
    close(lockfd);
    /* After the lock is released, so the committing session can take it. */
    if (staged) __atomic_store_n(&s_exec_pending, 1, __ATOMIC_RELEASE);
    return rc;
}

static void *update_thread(void *arg) {
    (void)arg;
    maybe_update(UPDATE_STAGE);
    __atomic_store_n(&s_bg_running, 0, __ATOMIC_RELEASE);
    return NULL;
}

void update_start_background(void) {
    if (!g_auto_update || update_exec_pending() || __atomic_exchange_n(&s_bg_running, 1, __ATOMIC_ACQ_REL)) return;
    pthread_t th;
    if (pthread_create(&th, NULL, update_thread, NULL) == 0) pthread_detach(th);
    else __atomic_store_n(&s_bg_running, 0, __ATOMIC_RELEASE);
}
