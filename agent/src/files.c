/* Fuse connector: read-side filesystem ops (paths, text/binary reads, listings). */
#include <dirent.h>
#include <fcntl.h>
#include <pwd.h>
#include <sys/stat.h>
#include <sys/types.h>

#include <openssl/evp.h>

#include "agent.h"
#include "files.h"

char *read_binary(const char *path, int offset, int limit) {
    if (offset < 0 || offset > BINARY_FILE_MAX || limit < 1 || limit > BINARY_CHUNK_MAX) {
        errno = EINVAL;
        return NULL;
    }
    char expanded[PATH_MAX];
    if (expand_home(path, expanded, sizeof(expanded)) != 0) return NULL;
    int fd = open(expanded, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return NULL;
    struct stat before, after, current;
    if (fstat(fd, &before) != 0 || !S_ISREG(before.st_mode)) {
        close(fd); errno = EINVAL; return NULL;
    }
    if (before.st_size > BINARY_FILE_MAX) {
        close(fd); errno = EFBIG; return NULL;
    }
    /* Only the requested range is read. Hashing the whole file for every
     * chunk made an image read quadratic (a 20 MiB file: ~640 chunks, each
     * re-reading and hashing all of it). The server only compares identity
     * between chunks, so the stat identity below serves. */
    unsigned char *data = malloc((size_t)limit);
    char *encoded = malloc(4 * (((size_t)limit + 2) / 3) + 1);
    char identity[128];
    int used = 0, failure = 0;
    if (!data || !encoded) failure = ENOMEM;
    while (!failure && used < limit) {
        ssize_t n = pread(fd, data + used, (size_t)(limit - used), (off_t)offset + used);
        if (n < 0) {
            if (errno == EINTR) continue;
            failure = errno; break;
        }
        if (!n) break;
        used += (int)n;
    }
    if (!failure && (fstat(fd, &after) != 0 || stat(expanded, &current) != 0 ||
        before.st_dev != current.st_dev || before.st_ino != current.st_ino ||
        after.st_size != before.st_size || (offset + used < before.st_size && used < limit) ||
        before.st_mtim.tv_sec != after.st_mtim.tv_sec || before.st_mtim.tv_nsec != after.st_mtim.tv_nsec ||
        before.st_ctim.tv_sec != after.st_ctim.tv_sec || before.st_ctim.tv_nsec != after.st_ctim.tv_nsec)) failure = ESTALE;
    close(fd);
    if (failure) {
        free(data);
        free(encoded);
        errno = failure;
        return NULL;
    }
    int size = (int)before.st_size;
    snprintf(identity, sizeof(identity), "stat:%llx:%llx:%d:%lld.%09ld:%lld.%09ld",
             (unsigned long long)before.st_dev, (unsigned long long)before.st_ino, size,
             (long long)before.st_mtim.tv_sec, before.st_mtim.tv_nsec,
             (long long)before.st_ctim.tv_sec, before.st_ctim.tv_nsec);
    EVP_EncodeBlock((unsigned char *)encoded, data, used);
    free(data);
    cJSON *result = cJSON_CreateObject();
    cJSON_AddBoolToObject(result, "ok", 1);
    cJSON_AddStringToObject(result, "data", encoded);
    free(encoded);
    cJSON_AddNumberToObject(result, "size", size);
    cJSON_AddStringToObject(result, "identity", identity);
    cJSON_AddBoolToObject(result, "eof", offset + used >= size);
    char *json = cJSON_PrintUnformatted(result);
    cJSON_Delete(result);
    return json;
}

int read_file_slice(const char *path, int offset, int limit, char *out, size_t cap) {
    char expanded[PATH_MAX];
    if (expand_home(path, expanded, sizeof(expanded)) != 0) return -1;
    path = expanded;
    /* O_NONBLOCK: opening a FIFO with no writer would otherwise block this
       worker forever, before the regular-file check could reject it. */
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

static const char *home_dir(void) {
    const char *h = getenv("HOME");
    if (h && h[0]) return h;
    struct passwd *pw = getpwuid(getuid());
    if (pw && pw->pw_dir && pw->pw_dir[0]) return pw->pw_dir;
    return "/";
}

int expand_home(const char *path, char *out, size_t cap) {
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

#define LIST_SCAN_MAX 1500

struct browse_item {
    char *name;
    int dir;
};

/* Directories first, then by name. */
static int browse_cmp(const void *a, const void *b) {
    const struct browse_item *x = a, *y = b;
    if (x->dir != y->dir) return y->dir - x->dir;
    return strcmp(x->name, y->name);
}

static int name_cmp(const void *a, const void *b) {
    return strcmp(*(char *const *)a, *(char *const *)b);
}

char *browse_json(const char *path) {
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
    /* Up to LIST_SCAN_MAX entries are sorted and the first 400 kept: taking
     * the first 400 in readdir order showed an arbitrary subset. */
    struct browse_item *items = malloc(sizeof(*items) * LIST_SCAN_MAX);
    int n = 0;
    struct dirent *ent;
    while (items && (ent = readdir(d)) && n < LIST_SCAN_MAX) {
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
    if (n) qsort(items, (size_t)n, sizeof(*items), browse_cmp);
    for (int i = 0; i < n; i++) {
        if (i < 400) {
            cJSON *row = cJSON_CreateObject();
            cJSON_AddStringToObject(row, "name", items[i].name);
            cJSON_AddStringToObject(row, "kind", items[i].dir ? "dir" : "file");
            cJSON_AddItemToArray(arr, row);
        }
        free(items[i].name);
    }
    free(items);
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

int list_dir(const char *path, char *out, size_t cap) {
    char expanded[PATH_MAX];
    if (expand_home(path, expanded, sizeof(expanded)) != 0) return -1;
    path = expanded;
    DIR *d = opendir(path);
    if (!d) return -1;
    struct dirent *ent;
    size_t used = 0;
    out[0] = 0;
    /* Sorted before the cap so the server's page is the alphabetical start
     * (as the Windows connector), not an arbitrary readdir subset. */
    char **names = malloc(sizeof(*names) * LIST_SCAN_MAX);
    int n = 0;
    while (names && (ent = readdir(d)) && n < LIST_SCAN_MAX) {
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
    if (n) qsort(names, (size_t)n, sizeof(*names), name_cmp);
    for (int i = 0; i < n; i++) {
        size_t ln = strlen(names[i]);
        if (used + ln + 2 >= cap) break;
        memcpy(out + used, names[i], ln);
        used += ln;
        out[used++] = '\n';
        out[used] = 0;
    }
    for (int i = 0; i < n; i++) free(names[i]);
    free(names);
    return 0;
}
