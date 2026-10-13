#include <stdio.h>
#include <string.h>

#include "reconnect.h"

static int fail = 0;

static void expect_str(const char *got, const char *want, const char *label) {
    got = got ? got : "(null)";
    want = want ? want : "(null)";
    if (strcmp(got, want) != 0) {
        fprintf(stderr, "FAIL %s: got %s want %s\n", label, got, want);
        fail = 1;
    }
}

static void expect_int(int got, int want, const char *label) {
    if (got != want) {
        fprintf(stderr, "FAIL %s: got %d want %d\n", label, got, want);
        fail = 1;
    }
}

static void expect_null(const char *got, const char *label) {
    if (got != NULL) {
        fprintf(stderr, "FAIL %s: got %s want null\n", label, got);
        fail = 1;
    }
}

int main(void) {
    struct conn_watch w;
    int send_ping = 0;
    const char *drop;

    /* Ping timeout closes the connection and starts reconnect. */
    conn_watch_init(&w, 0, 0);
    drop = conn_watch_tick(&w, 19999, 19999, WAKE_NONE, &send_ping);
    expect_null(drop, "pre-ping");
    expect_int(send_ping, 0, "pre-ping send");
    drop = conn_watch_tick(&w, PING_INTERVAL_MS, PING_INTERVAL_MS, WAKE_NONE, &send_ping);
    expect_null(drop, "ping due");
    expect_int(send_ping, 1, "ping due send");
    conn_watch_sent_ping(&w, PING_INTERVAL_MS);
    drop = conn_watch_tick(&w, PING_INTERVAL_MS + PONG_TIMEOUT_MS - 1,
                           PING_INTERVAL_MS + PONG_TIMEOUT_MS - 1, WAKE_NONE, &send_ping);
    expect_null(drop, "pong still in window");
    drop = conn_watch_tick(&w, PING_INTERVAL_MS + PONG_TIMEOUT_MS,
                           PING_INTERVAL_MS + PONG_TIMEOUT_MS, WAKE_NONE, &send_ping);
    expect_str(drop, "ping timeout", "missed pong");

    /* A pong (or any rx) resets the timer; no timeout. */
    conn_watch_init(&w, 0, 0);
    drop = conn_watch_tick(&w, PING_INTERVAL_MS, PING_INTERVAL_MS, WAKE_NONE, &send_ping);
    expect_int(send_ping, 1, "second ping due");
    conn_watch_sent_ping(&w, PING_INTERVAL_MS);
    conn_watch_got_rx(&w, PING_INTERVAL_MS + 5000);
    drop = conn_watch_tick(&w, PING_INTERVAL_MS + PONG_TIMEOUT_MS + 1000,
                           PING_INTERVAL_MS + PONG_TIMEOUT_MS + 1000, WAKE_NONE, &send_ping);
    expect_null(drop, "pong prevented timeout");
    expect_int(send_ping, 0, "not due yet after pong");

    /* Resume callback closes immediately — not after TCP keepalive. */
    conn_watch_init(&w, 1000, 1000);
    drop = conn_watch_tick(&w, 1000, 1000, WAKE_RESUME, &send_ping);
    expect_str(drop, "resume", "resume callback");
    expect_int(send_ping, 0, "resume does not ping");

    /* CLOCK_BOOTTIME jumped (suspend) while monotonic barely moved. */
    conn_watch_init(&w, 1000, 1000);
    drop = conn_watch_tick(&w, 1500, 1000 + 5 * 60 * 1000, WAKE_NONE, &send_ping);
    expect_str(drop, "resume", "boottime gap");

    /* No false resume on a normal 1s tick. */
    conn_watch_init(&w, 1000, 1000);
    drop = conn_watch_tick(&w, 2000, 2000, WAKE_NONE, &send_ping);
    expect_null(drop, "no suspend");

    /* Network-up wake reconnects immediately. */
    conn_watch_init(&w, 0, 0);
    drop = conn_watch_tick(&w, 100, 100, WAKE_NETWORK, &send_ping);
    expect_str(drop, "network change", "network callback");

    /* Backoff: immediate, 1s, 2s, 5s, 10s, 15s, cap 30s. */
    expect_int(reconnect_backoff_ms(0, 0), 0, "backoff 0");
    expect_int(reconnect_backoff_ms(1, 0), 1000, "backoff 1");
    expect_int(reconnect_backoff_ms(2, 0), 2000, "backoff 2");
    expect_int(reconnect_backoff_ms(3, 0), 5000, "backoff 3");
    expect_int(reconnect_backoff_ms(4, 0), 10000, "backoff 4");
    expect_int(reconnect_backoff_ms(5, 0), 15000, "backoff 5");
    expect_int(reconnect_backoff_ms(6, 0), 30000, "backoff 6");
    expect_int(reconnect_backoff_ms(99, 0), 30000, "backoff cap");
    int j = reconnect_backoff_ms(1, 100);
    if (j < 1000 || j > 1000 + 250) {
        fprintf(stderr, "FAIL jitter range: %d\n", j);
        fail = 1;
    }

    /* Dead after one missed pong is tens of seconds, not TCP keepalive (often 7200s). */
    if (PING_INTERVAL_MS + PONG_TIMEOUT_MS > 60000) {
        fprintf(stderr, "FAIL ping/pong window too long: %d\n", PING_INTERVAL_MS + PONG_TIMEOUT_MS);
        fail = 1;
    }

    /* Two-miss configuration still drops on the second timeout, not keepalive. */
    conn_watch_init(&w, 0, 0);
    w.miss_limit = 2;
    drop = conn_watch_tick(&w, PING_INTERVAL_MS, PING_INTERVAL_MS, WAKE_NONE, &send_ping);
    expect_int(send_ping, 1, "limit2 ping");
    conn_watch_sent_ping(&w, PING_INTERVAL_MS);
    drop = conn_watch_tick(&w, PING_INTERVAL_MS + PONG_TIMEOUT_MS,
                           PING_INTERVAL_MS + PONG_TIMEOUT_MS, WAKE_NONE, &send_ping);
    expect_null(drop, "first miss with limit 2");
    expect_int(send_ping, 1, "second ping after miss");
    conn_watch_sent_ping(&w, PING_INTERVAL_MS + PONG_TIMEOUT_MS);
    drop = conn_watch_tick(&w, PING_INTERVAL_MS + 2 * PONG_TIMEOUT_MS,
                           PING_INTERVAL_MS + 2 * PONG_TIMEOUT_MS, WAKE_NONE, &send_ping);
    expect_str(drop, "ping timeout", "second miss");

    /* A session closed right after hello_ok keeps backing off; a healthy one resets. */
    expect_int(fail_index_after_session(0, 50, 0), 1, "instant close backs off");
    expect_int(fail_index_after_session(6, 50, 0), 6, "instant close capped");
    expect_int(fail_index_after_session(4, 50, 1), 0, "frame after hello resets");
    expect_int(fail_index_after_session(4, HEALTHY_SESSION_MS, 0), 0, "long session resets");

    /* Reconnecting pulls the hourly update check in; it never pushes it out. */
    expect_int((int)update_check_after_connect(3600000, 1000), 1000 + UPDATE_SOON_MS, "update soon after connect");
    expect_int((int)update_check_after_connect(5000, 1000), 5000, "earlier check kept");

    expect_str(wake_reason_str(WAKE_RESUME), "resume", "wake str resume");
    expect_str(wake_reason_str(WAKE_NETWORK), "network change", "wake str net");

    if (fail) return 1;
    puts("ok");
    return 0;
}
