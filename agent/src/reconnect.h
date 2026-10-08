#ifndef FUSE_RECONNECT_H
#define FUSE_RECONNECT_H

#include <stdint.h>

/* Application-level liveness. Do not rely on OS TCP keepalive. */
#define PING_INTERVAL_MS 20000
#define PONG_TIMEOUT_MS 15000
#define PING_MISS_LIMIT 1
#define SUSPEND_GAP_MS 2000
#define BACKOFF_CAP_MS 30000
#define CONNECT_TIMEOUT_MS 10000
#define IO_TIMEOUT_MS 10000

enum wake_reason {
    WAKE_NONE = 0,
    WAKE_RESUME = 1,
    WAKE_NETWORK = 2
};

struct conn_watch {
    int64_t last_rx_ms;
    int64_t ping_sent_ms; /* 0 = no outstanding ping */
    int missed_pongs;
    int64_t last_mono_ms;
    int64_t last_boot_ms;
    int64_t suspend_offset_ms; /* boot - mono at last tick */
    int ping_interval_ms;
    int pong_timeout_ms;
    int miss_limit;
    int suspend_gap_ms;
};

int64_t now_mono_ms(void);
int64_t now_boot_ms(void);

void conn_watch_init(struct conn_watch *w, int64_t mono_ms, int64_t boot_ms);
void conn_watch_got_rx(struct conn_watch *w, int64_t mono_ms);
void conn_watch_sent_ping(struct conn_watch *w, int64_t mono_ms);

/* Returns a drop reason, or NULL to keep the session.
 * *send_ping is set when an application ping should be written. */
const char *conn_watch_tick(struct conn_watch *w, int64_t mono_ms, int64_t boot_ms, int wake,
                            int *send_ping);

int conn_watch_wait_ms(const struct conn_watch *w, int64_t mono_ms);

/* Immediate retry, then 1s, 2s, 5s, 10s, 15s, cap 30s. jitter_rand adds 0..base/4. */
int reconnect_backoff_ms(int fail_index, unsigned jitter_rand);

/* A server restart drops every session, and the top-of-loop update check
 * runs while that server is still down: check again soon after connecting. */
#define UPDATE_SOON_MS 60000
int64_t update_check_after_connect(int64_t next_ms, int64_t now_ms);

const char *wake_reason_str(int wake);

#endif
