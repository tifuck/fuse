/* Deterministic regressions for connector lifecycle internals:
 *   - wake pipe protocol (single consumer; reason latest-wins)
 *   - stop is level-triggered on a dedicated pipe and survives a saturated
 *     shared wake queue (SIGTERM must never be lost to EAGAIN)
 *   - idle admission sampling order (jobs before outputs)
 *   - cross-process update ownership (flock + exclusive staging)
 *   - connection lifetime: shutdown targets only the live fd, under the
 *     connection mutex, and never a retired fd
 * Harness mirrors tests/test_files.c: the connector is included with main
 * renamed, so the static internals are directly exercisable. */
#define main fuse_agent_program_main
#include "../src/fuse-agent.c"
#undef main
#include <assert.h>
#include <sys/socket.h>

static void wr(int fd, const char *b, size_t n) {
    assert(write(fd, b, n) == (ssize_t)n);
}

int main(void) {
    /* --- wake pipe protocol --- */
    int p[2], sp[2];
    /* The connector creates its wake pipe nonblocking (start_watch_thread);
     * consume_wake relies on read() returning EAGAIN when drained. */
    assert(pipe2(p, O_NONBLOCK) == 0);
    assert(pipe2(sp, O_NONBLOCK) == 0);
    g_wake_rd = p[0];
    g_wake_wr = p[1];
    g_main_stop_rd = sp[0];
    g_main_stop_wr = sp[1];
    consume_wake();
    assert(wake_take() == WAKE_NONE && !g_stop_main);

    char net = 2;
    wr(g_wake_wr, &net, 1);
    consume_wake();
    assert(wake_take() == WAKE_NETWORK);
    assert(wake_take() == WAKE_NONE); /* take consumed exactly once */

    char burst[] = {1, 1, 0}; /* resume wins, 0 is a pure wakeup */
    wr(g_wake_wr, burst, sizeof(burst));
    consume_wake();
    assert(g_stop_main == 0);
    assert(wake_take() == WAKE_RESUME);
    assert(wake_take() == WAKE_NONE);

    /* --- SIGTERM must survive a saturated wake queue --- */
    /* Fill the shared wake pipe with pure-wakeup zeros until EAGAIN. */
    char zero = 0;
    ssize_t wrote = 0;
    while (write(g_wake_wr, &zero, 1) == 1) wrote++;
    assert(wrote > 0);
    assert(errno == EAGAIN); /* the wake queue is genuinely full */
    /* The old design wrote the stop flag here and it was lost forever: */
    assert(write(g_wake_wr, &net, 1) == -1 && errno == EAGAIN);
    /* The dedicated stop pipe is independent and unsaturated: */
    char stop = 1;
    wr(g_main_stop_wr, &stop, 1);
    wr(g_main_stop_wr, &stop, 1); /* several signals queue, one drain records */
    record_main_stop();
    assert(g_stop_main == 1);
    /* A second record stays sticky and the stop pipe is fully drained. */
    record_main_stop();
    char c;
    assert(read(g_main_stop_rd, &c, 1) == -1 && errno == EAGAIN);
    consume_wake(); /* still drains the saturated zeros without a reason */
    assert(wake_take() == WAKE_NONE);
    close(p[0]);
    close(p[1]);
    close(sp[0]);
    close(sp[1]);
    g_wake_rd = g_wake_wr = g_main_stop_rd = g_main_stop_wr = -1;

    /* --- idle admission sampling --- */
    assert(agent_idle() == 1);
    g_busy = 1;
    assert(agent_idle() == 0); /* running job */
    g_busy = 0;
    g_out_n = 1;
    assert(agent_idle() == 0); /* undelivered result */
    g_out_n = 0;
    g_qcount = 1;
    assert(agent_idle() == 0); /* queued job */
    g_qcount = 0;
    assert(agent_idle() == 1);

    /* --- update ownership and staging --- */
    char self[] = "/tmp/fuse-lifecycle-bin";
    char lp[PATH_MAX];
    int l1 = update_lock_acquire(self, lp, sizeof(lp));
    assert(l1 >= 0);
    assert(update_lock_acquire(self, lp, sizeof(lp)) < 0); /* second owner refused */
    close(l1);
    int l2 = update_lock_acquire(self, lp, sizeof(lp));
    assert(l2 >= 0); /* released lock is re-acquirable */
    close(l2);
    unlink(lp);

    char s1[PATH_MAX], s2[PATH_MAX];
    assert(format_update_staging(s1, sizeof(s1), self) == 0);
    assert(format_update_staging(s2, sizeof(s2), self) == 0);
    int f1 = mkstemp(s1), f2 = mkstemp(s2);
    assert(f1 >= 0 && f2 >= 0);
    assert(strcmp(s1, s2) != 0); /* O_EXCL staging is never shared */
    close(f1);
    close(f2);
    unlink(s1);
    unlink(s2);

    /* --- connection lifetime --- */
    int a[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, a) == 0);
    g_live_fd = a[0];
    request_reconnect(WAKE_NETWORK); /* live fd: peer must observe shutdown */
    assert(read(a[1], &c, 1) == 0);
    close(a[0]);
    close(a[1]);
    g_live_fd = -1;

    int b2[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, b2) == 0);
    g_live_fd = b2[0];
    set_live_fd(-1); /* main thread retired the fd first */
    request_reconnect(WAKE_RESUME);
    fcntl(b2[1], F_SETFL, O_NONBLOCK);
    errno = 0;
    assert(read(b2[1], &c, 1) == -1 && errno == EAGAIN); /* untouched */
    close(b2[0]);
    close(b2[1]);
    g_live_fd = -1;

    /* --- reconnect backoff contract (unchanged) --- */
    assert(reconnect_backoff_ms(1, 0) == 1000);
    assert(reconnect_backoff_ms(99, 0) == BACKOFF_CAP_MS);

    unlink(self);
    unlink("/tmp/fuse-lifecycle-bin.update.lock");
    printf("test_lifecycle: OK\n");
    return 0;
}
