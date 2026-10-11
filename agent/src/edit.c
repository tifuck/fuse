/* Fuse connector: write-side filesystem ops (atomic writes, exact edits). */
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "agent.h"
#include "edit.h"
#include "files.h"

/* The process umask without the umask(0)/umask(old) race (other threads may
 * be creating files): Linux reports it in /proc/self/status. */
static mode_t current_umask(void) {
    unsigned int um = 022;
    FILE *f = fopen("/proc/self/status", "r");
    if (f) {
        char line[256];
        while (fgets(line, sizeof(line), f)) {
            if (sscanf(line, "Umask:\t%o", &um) == 1) break;
        }
        fclose(f);
    }
    return (mode_t)(um & 0777);
}

static int write_all_fd(int fd, const char *content, size_t n) {
    size_t done = 0;
    while (done < n) {
        ssize_t wrote = write(fd, content + done, n - done);
        if (wrote < 0 && errno == EINTR) continue;
        if (wrote <= 0) return -1;
        done += (size_t)wrote;
    }
    return 0;
}

/* Rewrite an existing regular file in place: same inode, so hard links,
 * owner, ACLs and xattrs are untouched. Not atomic; used only when a sibling
 * temp file would break a hard link or cannot be created at all. */
static int write_in_place(const char *path, const char *content, size_t n) {
    int fd = open(path, O_WRONLY | O_TRUNC | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    if (write_all_fd(fd, content, n) != 0 || fsync(fd) != 0) {
        int e = errno;
        close(fd);
        errno = e;
        return -1;
    }
    return close(fd);
}

/* mkdir -p for a new file's parent; the umask applies as for any mkdir. */
static int mkdir_parents(const char *dir) {
    char buf[PATH_MAX];
    size_t n = strlen(dir);
    if (n == 0 || n >= sizeof(buf)) return -1;
    memcpy(buf, dir, n + 1);
    for (size_t i = 1; i <= n; i++) {
        if (buf[i] != '/' && buf[i] != 0) continue;
        char saved = buf[i];
        buf[i] = 0;
        if (mkdir(buf, 0777) != 0 && errno != EEXIST) return -1;
        buf[i] = saved;
    }
    return 0;
}

int write_file_atomic_limit(const char *path, const char *content, long max) {
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
    size_t n = content ? strlen(content) : 0;
    struct stat st;
    int exists = 0;
    /* New files get what any editor would create (0666 minus the umask), not
     * mkstemp's 0600 that other service users cannot read. */
    mode_t mode = 0666 & ~current_umask();
    if (lstat(path, &st) == 0) {
        if (!S_ISREG(st.st_mode)) {
            errno = EINVAL;
            return -1;
        }
        exists = 1;
        mode = st.st_mode & 0777;
        /* A rename would split a hard-linked file into two copies. */
        if (st.st_nlink > 1) return write_in_place(path, content ? content : "", n);
    } else if (errno != ENOENT) {
        return -1;
    } else if (mkdir_parents(dir) != 0) {
        return -1;
    }
    if (snprintf(tmp, sizeof(tmp), "%s/.fuse-XXXXXX", dir) >= (int)sizeof(tmp)) return -1;
    int fd = mkstemp(tmp);
    if (fd < 0) {
        /* Directory not writable but the file is: edit the file itself. */
        if (exists && (errno == EACCES || errno == EPERM))
            return write_in_place(path, content ? content : "", n);
        return -1;
    }
    if (fchmod(fd, mode) != 0) {
        close(fd);
        unlink(tmp);
        return -1;
    }
    /* Keep the original owner where permitted (e.g. root editing a user's
     * file); an unprivileged connector cannot, and that is not an error. */
    if (exists && (st.st_uid != geteuid() || st.st_gid != getegid()))
        if (fchown(fd, st.st_uid, st.st_gid) != 0) { /* not permitted: keep ours */ }
    if (write_all_fd(fd, content ? content : "", n) != 0 || fsync(fd) != 0) {
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

int write_file_atomic(const char *path, const char *content) {
    return write_file_atomic_limit(path, content, WRITE_FILE_MAX);
}

int load_whole(const char *path, char **buf, size_t *len) {
    char expanded[PATH_MAX];
    if (expand_home(path, expanded, sizeof(expanded)) != 0) return -1;
    path = expanded;
    /* Non-blocking open + regular-file check: a FIFO must not hang a worker. */
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode)) {
        close(fd);
        errno = EINVAL;
        return -1;
    }
    FILE *f = fdopen(fd, "rb");
    if (!f) {
        close(fd);
        return -1;
    }
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
char *crlf_expand(const char *s, size_t n) {
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

/* Apply one exact replacement to a NUL-terminated heap buffer, replacing it
 * on success. LF-only old/new against a CRLF text are converted to CRLF
 * (read_file hides carriage returns) — deterministic, never fuzzy. */
int apply_edit(char **bufp, size_t *lenp, const char *old, const char *newv, int all,
                      long *reps_out, char *why, size_t whylen) {
    char *buf = *bufp;
    size_t len = *lenp;
    if (!old || !old[0]) {
        snprintf(why, whylen, "old_string required");
        return -1;
    }
    if (strcmp(old, newv) == 0) {
        snprintf(why, whylen, "old_string and new_string are identical");
        return -1;
    }
    char *old_crlf = NULL, *new_crlf = NULL;
    const char *old_use = old, *new_use = newv;
    size_t oldn = strlen(old), newn = strlen(newv);
    long count = 0;
    for (const char *p = buf; (p = strstr(p, old)); p += oldn) count++;
    if (count == 0 && memmem(buf, len, "\r\n", 2) && !memchr(old, '\r', oldn) && memchr(old, '\n', oldn)) {
        old_crlf = crlf_expand(old, oldn);
        new_crlf = crlf_expand(newv, newn);
        if (old_crlf && new_crlf) {
            size_t o2 = strlen(old_crlf);
            for (const char *q = buf; (q = strstr(q, old_crlf)); q += o2) count++;
            if (count) {
                old_use = old_crlf;
                new_use = new_crlf;
                oldn = o2;
                newn = strlen(new_crlf);
            }
        }
    }
    if (count == 0) {
        free(old_crlf); free(new_crlf);
        snprintf(why, whylen, "old_string not found");
        return -1;
    }
    if (count > 1 && !all) {
        free(old_crlf); free(new_crlf);
        snprintf(why, whylen, "old_string is not unique (%ld occurrences)", count);
        return -1;
    }
    long reps = all ? count : 1;
    long final = (long)len + reps * ((long)newn - (long)oldn);
    if (final > EDIT_FILE_MAX) {
        free(old_crlf); free(new_crlf);
        snprintf(why, whylen,
                 "replacement too large (maximum %ld bytes); apply the change with run_terminal_command",
                 EDIT_FILE_MAX);
        return -1;
    }
    char *nb = malloc((size_t)final + 1);
    if (!nb) {
        free(old_crlf); free(new_crlf);
        snprintf(why, whylen, "out of memory");
        return -1;
    }
    char *w = nb;
    const char *r = buf;
    for (long left = reps; left; left--) {
        const char *hit = strstr(r, old_use);
        if (!hit) break;
        size_t pre = (size_t)(hit - r);
        memcpy(w, r, pre);
        w += pre;
        memcpy(w, new_use, newn);
        w += newn;
        r = hit + oldn;
    }
    strcpy(w, r);
    free(old_crlf); free(new_crlf);
    free(buf);
    *bufp = nb;
    *lenp = strlen(nb);
    *reps_out = reps;
    return 0;
}
