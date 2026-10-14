/* Fuse connector: child process execution with caps, timeout and cancel. */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>

#include "agent.h"
#include "exec.h"
#include "jobs.h"
#include "outq.h"

#define EXEC_READ 32768

/* Readable once the child exits (Linux 5.3+); -1 falls back to polling. */
static int open_pidfd(pid_t pid) {
#ifdef SYS_pidfd_open
    return (int)syscall(SYS_pidfd_open, pid, 0);
#else
    (void)pid;
    return -1;
#endif
}

/* Stream one pipe read, holding back a UTF-8 sequence split across reads so
 * the character arrives whole with the next read (n == 0 flushes the rest). */
static int send_utf8_chunk(const char *id, const char *stream, char *carry, size_t *carry_n,
                           const char *data, size_t n) {
    char buf[EXEC_READ + 4];
    size_t total = *carry_n;
    memcpy(buf, carry, total);
    if (n) memcpy(buf + total, data, n);
    total += n;
    size_t tail = n ? utf8_incomplete_tail(buf, total) : 0;
    *carry_n = tail;
    memcpy(carry, buf + total - tail, tail);
    return send_chunk(id, stream, buf, total - tail);
}

int run_exec(const char *cwd, const char *cmd, int timeout_ms, char *out, size_t ocap, char *errb, size_t ecap, int *code, struct job *job, int *timed_out, int stream, const char *job_id, int *clipped, size_t *out_len, size_t *err_len) {
    int outp[2], errp[2];
    /* O_CLOEXEC: exec'd commands (and daemons they spawn) must not inherit
     * job pipe fds. dup2() in the child clears CLOEXEC on stdout/stderr. */
    if (pipe2(outp, O_CLOEXEC) != 0) return -1;
    if (pipe2(errp, O_CLOEXEC) != 0) {
        close(outp[0]);
        close(outp[1]);
        return -1;
    }
    pid_t pid = fork();
    if (pid < 0) {
        close(outp[0]);
        close(outp[1]);
        close(errp[0]);
        close(errp[1]);
        return -1;
    }
    if (pid == 0) {
        setpgid(0, 0);
        /* The agent ignores SIGPIPE for its own socket writes and SIG_IGN
         * survives fork+exec; children need the default back or `| head`
         * pipelines end in an EPIPE "write error" exit 2 instead of 141. */
        signal(SIGPIPE, SIG_DFL);
        /* Never the agent's own stdin (a tty when run by hand). */
        int devnull = open("/dev/null", O_RDONLY);
        if (devnull > 0) {
            dup2(devnull, 0);
            close(devnull);
        }
        int chdir_err = 0;
        if (cwd && cwd[0] && chdir(cwd) != 0) chdir_err = errno;
        dup2(outp[1], 1);
        dup2(errp[1], 2);
        close(outp[0]);
        close(outp[1]);
        close(errp[0]);
        close(errp[1]);
        if (chdir_err) {
            dprintf(2, "fuse-agent: chdir %s: %s\n", cwd, strerror(chdir_err));
            _exit(127);
        }
        execl("/bin/bash", "bash", "--noprofile", "--norc", "-c", cmd ? cmd : "", (char *)NULL);
        dprintf(2, "fuse-agent: exec bash: %s\n", strerror(errno));
        _exit(127);
    }
    setpgid(pid, pid);
    if (job) {
        pthread_mutex_lock(&g_job_mu);
        job->pid = pid;
        int cancel = job->cancel;
        pthread_mutex_unlock(&g_job_mu);
        if (cancel) kill(-pid, SIGKILL);
    }
    close(outp[1]);
    close(errp[1]);
    fcntl(outp[0], F_SETFL, O_NONBLOCK);
    fcntl(errp[0], F_SETFL, O_NONBLOCK);
    /* Child exit wakes the poll at once; pipes leave the set at EOF so a
     * command that closes its own stdout/stderr cannot spin this loop, and
     * a background grandchild holding them cannot delay the result. */
    int pidfd = open_pidfd(pid);
    int out_open = 1, err_open = 1;
    size_t o = 0, e = 0;
    if (ocap) out[0] = 0;
    if (ecap) errb[0] = 0;
    struct timespec start;
    clock_gettime(CLOCK_MONOTONIC, &start);
    int status = 0, done = 0, killed = 0, by_timeout = 0;
    int out_clipped = 0, err_clipped = 0;
    long kill_ms = 0, reap_ms = 0;
    char out_carry[4], err_carry[4];
    size_t out_carry_n = 0, err_carry_n = 0;
    while (done < 2) {
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        long elapsed = (now.tv_sec - start.tv_sec) * 1000L + (now.tv_nsec - start.tv_nsec) / 1000000L;
        if (!done && !killed && job_cancelled(job)) {
            kill(-pid, SIGKILL);
            killed = 1;
            kill_ms = elapsed;
        }
        if (!done && !killed && timeout_ms > 0 && elapsed >= timeout_ms) {
            kill(-pid, SIGKILL);
            killed = 1;
            by_timeout = 1;
            kill_ms = elapsed;
        }
        /* SIGKILL is not enough for D-state (NFS hang, ptrace): never let an
         * unkillable child wedge a worker thread forever. Abandon the reap;
         * the slot is released either way and one zombie is the lesser evil. */
        if (killed && elapsed - kill_ms >= 10000) {
            fprintf(stderr, "fuse-agent: child %d unkillable after SIGKILL; abandoning\n", (int)pid);
            if (stream) {
                send_utf8_chunk(job_id, "out", out_carry, &out_carry_n, NULL, 0);
                send_utf8_chunk(job_id, "err", err_carry, &err_carry_n, NULL, 0);
            }
            close(outp[0]);
            close(errp[0]);
            if (pidfd >= 0) close(pidfd);
            if (code) *code = -1; /* no reap: remote outcome remains unconfirmed */
            if (timed_out) *timed_out = by_timeout;
            if (clipped) *clipped = out_clipped || err_clipped;
            if (out_len) *out_len = o;
            if (err_len) *err_len = e;
            return 0;
        }
        /* Wake for the deadline, the unkillable cutoff, or (at most each
         * second) a cancel flag; the session also SIGKILLs a cancelled job
         * directly, which the pidfd reports at once. */
        long wait_ms = 1000;
        if (done) wait_ms = 0; /* reaped: take what is buffered, never wait on a grandchild */
        else if (killed) wait_ms = 10000 - (elapsed - kill_ms);
        else if (timeout_ms > 0 && timeout_ms - elapsed < wait_ms) wait_ms = timeout_ms - elapsed;
        if (pidfd < 0 && wait_ms > 100) wait_ms = 100;
        if (wait_ms < 0) wait_ms = 0;
        /* Streamed output waits for queue room instead of being dropped: the
         * child blocks on its full pipe until the session drains, while the
         * timeout and cancel above still apply. */
        int held = stream && !out_has_room();
        if (held) {
            out_wait_room(wait_ms < 100 ? (int)wait_ms : 100);
            held = !out_has_room();
        } else {
            struct pollfd pfd[3];
            int nfd = 0;
            if (out_open) pfd[nfd++] = (struct pollfd){outp[0], POLLIN, 0};
            if (err_open) pfd[nfd++] = (struct pollfd){errp[0], POLLIN, 0};
            if (!done && pidfd >= 0) pfd[nfd++] = (struct pollfd){pidfd, POLLIN, 0};
            poll(pfd, (nfds_t)nfd, (int)wait_ms);
        }
        char tmp[EXEC_READ];
        ssize_t r = -1;
        for (int reads = 0; !held && out_open && reads < 64 && (r = read(outp[0], tmp, sizeof(tmp))) > 0; reads++) {
            size_t k = (size_t)r;
            if (stream && send_utf8_chunk(job_id, "out", out_carry, &out_carry_n, tmp, k) != 0) out_clipped = 1;
            if (stream && !out_has_room()) held = 1;
            if (o + k >= ocap) { out_clipped = 1; k = ocap - 1 - o; }
            if (k && !stream) {
                memcpy(out + o, tmp, k);
                o += k;
                out[o] = 0;
            }
        }
        if (r == 0) out_open = 0;
        r = -1;
        for (int reads = 0; !held && err_open && reads < 64 && (r = read(errp[0], tmp, sizeof(tmp))) > 0; reads++) {
            size_t k = (size_t)r;
            if (stream && send_utf8_chunk(job_id, "err", err_carry, &err_carry_n, tmp, k) != 0) err_clipped = 1;
            if (stream && !out_has_room()) held = 1;
            if (e + k >= ecap) { err_clipped = 1; k = ecap - 1 - e; }
            if (k && !stream) {
                memcpy(errb + e, tmp, k);
                e += k;
                errb[e] = 0;
            }
        }
        if (r == 0) err_open = 0;
        if (done) {
            /* Final bounded drain after reap. Output held back for queue room
             * is waited for, up to 10 s, then flagged rather than lost. */
            if (!held) done = 2;
            else if (elapsed - reap_ms >= 10000) {
                out_clipped = err_clipped = 1;
                done = 2;
            }
            continue;
        }
        pthread_mutex_lock(&g_job_mu);
        pid_t w = waitpid(pid, &status, WNOHANG);
        if (w == pid && job) job->pid = 0; /* do not signal a re-used pid after reap */
        pthread_mutex_unlock(&g_job_mu);
        if (w == pid) {
            done = 1;
            reap_ms = elapsed;
        }
    }
    if (stream) {
        send_utf8_chunk(job_id, "out", out_carry, &out_carry_n, NULL, 0);
        send_utf8_chunk(job_id, "err", err_carry, &err_carry_n, NULL, 0);
    }
    close(outp[0]);
    close(errp[0]);
    if (pidfd >= 0) close(pidfd);
    if (!by_timeout && timeout_ms > 0 && !job_cancelled(job)) {
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        long elapsed = (now.tv_sec - start.tv_sec) * 1000L + (now.tv_nsec - start.tv_nsec) / 1000000L;
        if (elapsed >= timeout_ms) by_timeout = 1;
    }
    const char *mark = "\n[fuse-agent: output truncated]\n";
    if (clipped) *clipped = out_clipped || err_clipped;
    if (stream) {
        if (out_clipped) snprintf(out, ocap, "%s", mark);
        if (err_clipped) snprintf(errb, ecap, "%s", mark);
        out_clipped = err_clipped = 0;
    }
    size_t marklen = strlen(mark);
    if (out_clipped && ocap > marklen + 1) {
        size_t end = ocap - marklen - 1;
        while (end && ((unsigned char)out[end] & 0xc0) == 0x80) end--;
        memcpy(out + end, mark, marklen + 1);
        o = end + marklen;
    }
    if (err_clipped && ecap > marklen + 1) {
        size_t end = ecap - marklen - 1;
        while (end && ((unsigned char)errb[end] & 0xc0) == 0x80) end--;
        memcpy(errb + end, mark, marklen + 1);
        e = end + marklen;
    }
    /* A signal death the agent did not cause (OOM killer, SIGSEGV) is told,
     * not left as a bare exit code. */
    if (WIFSIGNALED(status) && !killed && ecap) {
        if (stream) e = strlen(errb);
        char note[48];
        size_t nl = (size_t)snprintf(note, sizeof(note), "%s[killed by signal %d]\n",
                                     e && errb[e - 1] != '\n' ? "\n" : "", WTERMSIG(status));
        if (nl < ecap) {
            if (e + nl >= ecap) {
                e = ecap - nl - 1;
                while (e && ((unsigned char)errb[e] & 0xc0) == 0x80) e--;
            }
            memcpy(errb + e, note, nl + 1);
            e += nl;
        }
    }
    if (out_len) *out_len = stream ? strlen(out) : o;
    if (err_len) *err_len = stream ? strlen(errb) : e;
    if (WIFEXITED(status)) *code = WEXITSTATUS(status);
    /* Shell convention 128+N: an OOM kill (137) or SIGSEGV (139) must not
     * read as an ordinary exit 1. */
    else if (WIFSIGNALED(status)) *code = 128 + WTERMSIG(status);
    else *code = 1;
    if (timed_out) *timed_out = by_timeout;
    return 0;
}
