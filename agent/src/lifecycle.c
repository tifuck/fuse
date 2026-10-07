/* Fuse connector: process lifecycle - wake/stop pipes, signals, live-connection
 * fd, and the resume/network watch thread (netlink, logind, NetworkManager). */
#include <dlfcn.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/types.h>
#ifdef __linux__
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#endif

#include "agent.h"
#include "lifecycle.h"
#include "reconnect.h"

/* Wake/stop state crosses threads and signal contexts ONLY through pipes:
 * write(2) is async-signal-safe, and a single reader (the session-loop
 * thread) derives the flags below from the bytes. No shared variable is
 * written cross-thread, so correctness never depends on lock-free atomics.
 * Shared wake pipe bytes: 1 = resume, 2 = network change, 0 = pure wakeup.
 * Stop does NOT travel on the wake pipe: out_wake can fill it with zeros and
 * a nonblocking stop write would then hit EAGAIN and be lost forever. Stop
 * is level-triggered on dedicated, unsaturable stop pipes that only on_sig
 * writes; record_main_stop() drains one while setting the sticky flag. */
int g_stop_main = 0;    /* main-thread only; set via stop pipe */

int g_wake_reason = 0;  /* main-thread only; latest wake reason */

pthread_mutex_t g_conn_mu = PTHREAD_MUTEX_INITIALIZER;

int g_live_fd = -1;

int g_wake_rd = -1;

int g_wake_wr = -1;

int g_main_stop_rd = -1;

int g_main_stop_wr = -1;

static int g_watch_stop_rd = -1;

static int g_watch_stop_wr = -1;

static int g_watch_started = 0;

void on_sig(int sig) {
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

void on_usr1(int sig) {
    (void)sig;
    char b = 1;
    if (g_wake_wr >= 0) {
        (void)!write(g_wake_wr, &b, 1);
    }
}

/* Single consumer: the session-loop thread. Derives the latest wake reason
 * from the shared wake pipe bytes. */
void consume_wake(void) {
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
void record_main_stop(void) {
    if (g_main_stop_rd >= 0) {
        char buf[64];
        while (read(g_main_stop_rd, buf, sizeof(buf)) > 0) {
        }
    }
    g_stop_main = 1;
}

/* Atomically consume-and-clear the pending wake reason (main thread only,
 * so no lost reset is possible against producers: they only write bytes). */
int wake_take(void) {
    int r = g_wake_reason;
    g_wake_reason = 0;
    return r;
}

void set_live_fd(int fd) {
    pthread_mutex_lock(&g_conn_mu);
    g_live_fd = fd;
    pthread_mutex_unlock(&g_conn_mu);
}

void request_reconnect(int reason) {
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

/* Create the wake/stop pipes before any signal handler is installed: a
 * SIGTERM that arrived before they existed would otherwise be dropped (the
 * handler only writes pipes). Idempotent. */
void init_signal_pipes(void) {
    int p[2], q[2], r[2];
    if (g_wake_rd < 0 && pipe2(p, O_CLOEXEC | O_NONBLOCK) == 0) {
        g_wake_rd = p[0];
        g_wake_wr = p[1];
    }
    if (g_main_stop_rd < 0 && pipe2(q, O_CLOEXEC | O_NONBLOCK) == 0) {
        g_main_stop_rd = q[0];
        g_main_stop_wr = q[1];
    }
    if (g_watch_stop_rd < 0 && pipe2(r, O_CLOEXEC) == 0) {
        g_watch_stop_rd = r[0];
        g_watch_stop_wr = r[1];
    }
}

void start_watch_thread(void) {
    if (g_watch_started) return;
    init_signal_pipes();
    pthread_t th;
    if (pthread_create(&th, NULL, watch_thread, NULL) == 0) {
        pthread_detach(th);
        g_watch_started = 1;
    }
}
