/* Fuse connector: self-update (manifest fetch, sha256 verify, flock, staging). */
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>

#include <openssl/evp.h>

#include "agent.h"
#include "config.h"
#include "net.h"
#include "reconnect.h"
#include "update.h"

static int http_get_file(const char *base, const char *path, int fd, size_t max_bytes) {
    char full[512];
    if (snprintf(full, sizeof(full), "%s%s", base, path) >= (int)sizeof(full)) return -1;
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
    /* Write through a dup so the caller always keeps sole ownership of fd and
     * closes it exactly once, on success and failure alike. */
    int wfd = dup(fd);
    FILE *f = wfd >= 0 ? fdopen(wfd, "wb") : NULL;
    if (!f) {
        if (wfd >= 0) close(wfd);
        tls_close(&t);
        return -1;
    }
    size_t total = 0;
    int too_big = 0;
    /* One deadline for the whole body: tls_read_some alone gives each stalled
     * read a fresh timeout, so a peer trickling bytes could hold the update
     * lock forever. */
    int64_t deadline = now_mono_ms() + 5 * 60 * 1000;
    if (got > hdr_end) {
        size_t add = got - hdr_end;
        if (total + add > max_bytes) too_big = 1;
        else if (fwrite(hdr + hdr_end, 1, add, f) != add) too_big = 1;
        else total += add;
    }
    char buf[8192];
    int k;
    while (!too_big && now_mono_ms() < deadline && (k = tls_read_some(&t, buf, sizeof(buf))) > 0) {
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
        tls_close(&t);
        return -1;
    }
    fflush(f);
    fsync(fd);
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

/* allow_exec: only the connected daemon may execv() into the new build - it
 * replaces itself (same PID, single daemon) and only from the idle-gated
 * call sites. A manual `fuse-agent update` runs in its own process and must
 * never become a second live daemon (two instances on one token replace each
 * other forever), so it stages the verified binary and leaves restart
 * ownership to the operator or systemd. */
int maybe_update(int allow_exec) {
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
    /* mkstemp, not a predictable path + fopen: a symlink planted in /tmp by
     * another local user must never be truncated by an updater running as
     * root. */
    snprintf(tmp, sizeof(tmp), "/tmp/fuse-manifest.XXXXXX");
    int mfd = mkstemp(tmp);
    if (mfd < 0) goto out;
    int fetched = http_get_file(g_url, "/agent/v1/manifest", mfd, 300000);
    close(mfd);
    mfd = -1;
    if (fetched != 0) goto out;
    /* /tmp is sticky and the file is ours 0600, so reopening by name cannot
     * be swapped by another user. */
    mfd = open(tmp, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (mfd < 0) goto out;
    struct stat st;
    if (fstat(mfd, &st) != 0 || st.st_size <= 0 || st.st_size >= 200000) goto out;
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
    /* The path goes into a raw HTTP request line: spaces or CR/LF in a
     * crafted manifest would inject extra headers. Require a clean path. */
    if (path[0] != '/' || !expect[0] || strpbrk(path, " \t\r\n\"?#")) goto out;
    if (update_is_self(self, expect)) goto out;
    if (format_update_staging(neu, sizeof(neu), self) != 0) goto out;
    int sfd = mkstemp(neu);
    if (sfd < 0) goto out;
    int got_bin = http_get_file(g_url, path, sfd, 128 * 1024 * 1024);
    close(sfd);
    if (got_bin != 0) {
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
    if (mfd >= 0) close(mfd);
    if (root) cJSON_Delete(root);
    free(body);
    if (tmp[0]) unlink(tmp);
    close(lockfd);
    return rc;
}
