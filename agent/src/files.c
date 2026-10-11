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
    int fd = open(expanded, O_RDONLY | O_NONBLOCK);
    if (fd < 0) return NULL;
    struct stat before, after, current;
    if (fstat(fd, &before) != 0 || !S_ISREG(before.st_mode)) {
        close(fd); errno = EINVAL; return NULL;
    }
    if (before.st_size > BINARY_FILE_MAX) {
        close(fd); errno = EFBIG; return NULL;
    }
    unsigned char buf[BINARY_CHUNK_MAX], data[BINARY_CHUNK_MAX], digest[EVP_MAX_MD_SIZE];
    char encoded[4 * ((BINARY_CHUNK_MAX + 2) / 3) + 1], identity[72];
    int size = 0, used = 0, failure = 0;
    unsigned int digest_len = 0;
    EVP_MD_CTX *hash = EVP_MD_CTX_new();
    if (!hash || EVP_DigestInit_ex(hash, EVP_sha256(), NULL) != 1) failure = EIO;
    while (!failure) {
        ssize_t n = read(fd, buf, sizeof(buf));
        if (n < 0) {
            if (errno == EINTR) continue;
            failure = errno; break;
        }
        if (!n) break;
        if (size + n > BINARY_FILE_MAX) { failure = EFBIG; break; }
        if (EVP_DigestUpdate(hash, buf, (size_t)n) != 1) { failure = EIO; break; }
        int lo = offset > size ? offset : size;
        int hi = offset + limit < size + n ? offset + limit : size + (int)n;
        if (lo < hi) {
            memcpy(data + used, buf + lo - size, (size_t)(hi - lo));
            used += hi - lo;
        }
        size += (int)n;
    }
    if (!failure && (fstat(fd, &after) != 0 || stat(expanded, &current) != 0 ||
        before.st_dev != current.st_dev || before.st_ino != current.st_ino ||
        before.st_size != size || after.st_size != size ||
        before.st_mtim.tv_sec != after.st_mtim.tv_sec || before.st_mtim.tv_nsec != after.st_mtim.tv_nsec ||
        before.st_ctim.tv_sec != after.st_ctim.tv_sec || before.st_ctim.tv_nsec != after.st_ctim.tv_nsec)) failure = ESTALE;
    close(fd);
    if (!failure && EVP_DigestFinal_ex(hash, digest, &digest_len) != 1) failure = EIO;
    EVP_MD_CTX_free(hash);
    if (failure) { errno = failure; return NULL; }
    memcpy(identity, "sha256:", 7);
    for (unsigned int i = 0; i < digest_len; i++) snprintf(identity + 7 + i * 2, 3, "%02x", digest[i]);
    EVP_EncodeBlock((unsigned char *)encoded, data, used);
    cJSON *result = cJSON_CreateObject();
    cJSON_AddBoolToObject(result, "ok", 1);
    cJSON_AddStringToObject(result, "data", encoded);
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

int list_dir(const char *path, char *out, size_t cap) {
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
