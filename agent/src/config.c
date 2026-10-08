/* Fuse connector: pairing credentials and the agent.conf file they live in. */
#include <fcntl.h>
#include <pwd.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "agent.h"
#include "config.h"

char g_url[256] = {0};

char g_token[192] = {0};

char g_code[128] = {0};

char g_config[PATH_MAX] = {0};

int g_auto_update = 0;

static void trim(char *s) {
    size_t n = strlen(s);
    while (n && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ')) s[--n] = 0;
}

int load_config(const char *path) {
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

int save_config(const char *path) {
    /* Values are written as bare "k=v" lines: a newline in either would
     * inject extra config keys on the next load. */
    if (strpbrk(g_url, "\r\n") || strpbrk(g_token, "\r\n")) return -1;
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

void default_config_path(char *dst, size_t cap, int system_wide) {
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

/* A system unit starts the connector without HOME, and the commands it runs
 * inherit that (go, pip and git config fail). Fill it from the passwd entry;
 * never override one that is set. */
void ensure_home_env(void) {
    const char *home = getenv("HOME");
    if (home && home[0]) return;
    struct passwd *pw = getpwuid(getuid());
    if (pw && pw->pw_dir && pw->pw_dir[0]) setenv("HOME", pw->pw_dir, 1);
}

void hostname_user(char *host, size_t hcap, char *user, size_t ucap) {
    if (gethostname(host, hcap) != 0) strncpy(host, "machine", hcap - 1);
    host[hcap - 1] = 0;
    struct passwd *pw = getpwuid(getuid());
    strncpy(user, pw && pw->pw_name ? pw->pw_name : "user", ucap - 1);
    user[ucap - 1] = 0;
}

const char *arch_sku(void) {
#if defined(__aarch64__)
    return "linux-arm64";
#else
    return "linux-amd64";
#endif
}
