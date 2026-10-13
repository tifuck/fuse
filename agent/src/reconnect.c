#include "reconnect.h"

#include <time.h>

static const int k_backoff_ms[] = {0, 1000, 2000, 5000, 10000, 15000, BACKOFF_CAP_MS};

static int64_t clock_ms(clockid_t id) {
    struct timespec ts;
    if (clock_gettime(id, &ts) != 0) return 0;
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

int64_t now_mono_ms(void) {
    return clock_ms(CLOCK_MONOTONIC);
}

int64_t now_boot_ms(void) {
#ifdef CLOCK_BOOTTIME
    return clock_ms(CLOCK_BOOTTIME);
#else
    return clock_ms(CLOCK_MONOTONIC);
#endif
}

void conn_watch_init(struct conn_watch *w, int64_t mono_ms, int64_t boot_ms) {
    w->last_rx_ms = mono_ms;
    w->ping_sent_ms = 0;
    w->missed_pongs = 0;
    w->last_mono_ms = mono_ms;
    w->last_boot_ms = boot_ms;
    w->suspend_offset_ms = boot_ms - mono_ms;
    w->ping_interval_ms = PING_INTERVAL_MS;
    w->pong_timeout_ms = PONG_TIMEOUT_MS;
    w->miss_limit = PING_MISS_LIMIT;
    w->suspend_gap_ms = SUSPEND_GAP_MS;
}

void conn_watch_got_rx(struct conn_watch *w, int64_t mono_ms) {
    w->last_rx_ms = mono_ms;
    w->ping_sent_ms = 0;
    w->missed_pongs = 0;
}

void conn_watch_sent_ping(struct conn_watch *w, int64_t mono_ms) {
    w->ping_sent_ms = mono_ms;
}

const char *conn_watch_tick(struct conn_watch *w, int64_t mono_ms, int64_t boot_ms, int wake,
                            int *send_ping) {
    if (send_ping) *send_ping = 0;
    if (wake == WAKE_RESUME) return "resume";
    if (wake == WAKE_NETWORK) return "network change";

    int64_t offset = boot_ms - mono_ms;
    if (w->last_boot_ms != 0) {
        int64_t d_boot = boot_ms - w->last_boot_ms;
        int64_t d_mono = mono_ms - w->last_mono_ms;
        int64_t gap = d_boot - d_mono;
        int64_t off_delta = offset - w->suspend_offset_ms;
        if (gap < 0) gap = 0;
        if (off_delta < 0) off_delta = 0;
        if (gap >= w->suspend_gap_ms || off_delta >= w->suspend_gap_ms) return "resume";
    }
    w->last_mono_ms = mono_ms;
    w->last_boot_ms = boot_ms;
    w->suspend_offset_ms = offset;

    if (w->ping_sent_ms) {
        if (mono_ms - w->ping_sent_ms >= w->pong_timeout_ms) {
            w->missed_pongs++;
            w->ping_sent_ms = 0;
            if (w->missed_pongs >= w->miss_limit) return "ping timeout";
        }
    }
    if (send_ping && !w->ping_sent_ms && mono_ms - w->last_rx_ms >= w->ping_interval_ms) {
        *send_ping = 1;
    }
    return NULL;
}

int conn_watch_wait_ms(const struct conn_watch *w, int64_t mono_ms) {
    int64_t next = w->last_rx_ms + w->ping_interval_ms;
    if (w->ping_sent_ms) next = w->ping_sent_ms + w->pong_timeout_ms;
    int64_t left = next - mono_ms;
    if (left < 50) left = 50;
    if (left > 1000) left = 1000;
    return (int)left;
}

int reconnect_backoff_ms(int fail_index, unsigned jitter_rand) {
    int n = (int)(sizeof(k_backoff_ms) / sizeof(k_backoff_ms[0]));
    if (fail_index < 0) fail_index = 0;
    if (fail_index >= n) fail_index = n - 1;
    int base = k_backoff_ms[fail_index];
    if (base <= 0) return 0;
    int span = base / 4;
    return base + (int)(jitter_rand % (unsigned)(span + 1));
}

int fail_index_after_session(int fail_index, int64_t lasted_ms, int got_frame) {
    if (got_frame || lasted_ms >= HEALTHY_SESSION_MS) return 0;
    return fail_index < 6 ? fail_index + 1 : 6;
}

int64_t update_check_after_connect(int64_t next_ms, int64_t now_ms) {
    int64_t soon = now_ms + UPDATE_SOON_MS;
    return next_ms < soon ? next_ms : soon;
}

const char *wake_reason_str(int wake) {
    if (wake == WAKE_RESUME) return "resume";
    if (wake == WAKE_NETWORK) return "network change";
    return "";
}
