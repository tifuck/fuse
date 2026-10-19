/* Fuse connector: CLI entry, systemd install/uninstall. */
#include <signal.h>
#include <sys/stat.h>
#include <sys/types.h>

#include <openssl/ssl.h>

#include "agent.h"
#include "config.h"
#include "lifecycle.h"
#include "net.h"
#include "session.h"
#include "update.h"

static int write_unit(int system_wide, const char *bin, const char *cfg) {
    /* bin and cfg are interpolated into a systemd unit: quotes or newlines
     * (from HOME or --config) would inject extra directives. */
    if (strpbrk(bin, "\"\n\r") || strpbrk(cfg, "\"\n\r")) return -1;
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

/* Temp file + rename: writing the installed path in place failed (ETXTBSY)
 * or truncated a live binary when reinstalling over a running connector. */
static int copy_self(const char *dest) {
    char self[PATH_MAX], tmp[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", self, sizeof(self) - 1);
    if (n < 0) return -1;
    self[n] = 0;
    if (snprintf(tmp, sizeof(tmp), "%s.new.XXXXXX", dest) >= (int)sizeof(tmp)) return -1;
    FILE *in = fopen(self, "rb");
    int ofd = in ? mkstemp(tmp) : -1;
    FILE *out = ofd >= 0 ? fdopen(ofd, "wb") : NULL;
    if (!out) {
        if (in) fclose(in);
        if (ofd >= 0) {
            close(ofd);
            unlink(tmp);
        }
        return -1;
    }
    char buf[8192];
    size_t k;
    int bad = 0;
    while (!bad && (k = fread(buf, 1, sizeof(buf), in)) > 0)
        if (fwrite(buf, 1, k, out) != k) bad = 1;
    bad |= ferror(in) || fflush(out) != 0 || fsync(ofd) != 0 || fchmod(ofd, 0755) != 0;
    fclose(in);
    if (fclose(out) != 0) bad = 1;
    if (bad || rename(tmp, dest) != 0) {
        unlink(tmp);
        return -1;
    }
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
        /* A fresh account may have neither ~/.local nor ~/.config yet. */
        char parent[PATH_MAX];
        if (snprintf(parent, sizeof(parent), "%s/.local", home) >= (int)sizeof(parent)) die("path too long");
        if (mkdir(parent, 0755) != 0 && errno != EEXIST) die("could not create %s", parent);
        if (snprintf(bindir, sizeof(bindir), "%s/.local/bin", home) >= (int)sizeof(bindir)) die("path too long");
        if (mkdir(bindir, 0755) != 0 && errno != EEXIST) die("could not create %s", bindir);
        if (snprintf(parent, sizeof(parent), "%s/.config", home) >= (int)sizeof(parent)) die("path too long");
        if (mkdir(parent, 0700) != 0 && errno != EEXIST) die("could not create %s", parent);
        char cfgdir[PATH_MAX];
        if (snprintf(cfgdir, sizeof(cfgdir), "%s/.config/fuse-agent", home) >= (int)sizeof(cfgdir)) die("path too long");
        if (mkdir(cfgdir, 0700) != 0 && errno != EEXIST) die("could not create %s", cfgdir);
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
        snprintf(cmd, sizeof(cmd), "systemctl --user daemon-reload && systemctl --user enable --now fuse-agent");
    }
    int st = system(cmd);
    if (st != 0) die("systemd enable failed (is systemd running?)");
    /* Linger is optional (boot start without a login); its failure must not
     * mask whether the service itself started. */
    if (!root)
        (void)!system("command -v loginctl >/dev/null && loginctl enable-linger \"$(id -un)\" >/dev/null 2>&1");
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
    ensure_home_env(); /* before any thread: setenv is not thread-safe */
    srand((unsigned)(time(NULL) ^ getpid()));
    setvbuf(stderr, NULL, _IOLBF, 0);
    /* No openssl.cnf: the static build's OPENSSLDIR is a writable /tmp path,
     * and a planted config could load any provider into this process. */
    OPENSSL_init_ssl(OPENSSL_INIT_NO_LOAD_CONFIG | OPENSSL_INIT_LOAD_SSL_STRINGS | OPENSSL_INIT_LOAD_CRYPTO_STRINGS,
                     NULL);
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
    init_signal_pipes();
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
        /* A manual update stages the verified binary and never execs a
         * second daemon or disturbs the running one. */
        return maybe_update(UPDATE_MANUAL);
    }
    if (strcmp(cmd, "run") != 0) {
        usage();
        return 1;
    }
    if (!g_url[0]) die("missing --url or config url");
    if (!g_token[0] && !g_code[0]) die("missing --code/--token");
    return session_loop();
}
