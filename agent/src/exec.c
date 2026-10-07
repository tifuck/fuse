/* Fuse connector: child process execution with caps, timeout and cancel. */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>

#include "agent.h"
#include "exec.h"
#include "jobs.h"
#include "outq.h"

int run_exec(const char *cwd, const char *cmd, int timeout_ms, char *out, size_t ocap, char *errb, size_t ecap, int *code, struct job *job, int *timed_out, int stream, const char *job_id, int *clipped) {
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
    size_t o = 0, e = 0;
    if (ocap) out[0] = 0;
    if (ecap) errb[0] = 0;
    struct timespec start;
    clock_gettime(CLOCK_MONOTONIC, &start);
    int status = 0, done = 0, killed = 0, by_timeout = 0;
    int out_clipped = 0, err_clipped = 0;
    long kill_ms = 0;
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
            close(outp[0]);
            close(errp[0]);
            if (code) *code = -1; /* no reap: remote outcome remains unconfirmed */
            if (timed_out) *timed_out = by_timeout;
            if (clipped) *clipped = out_clipped || err_clipped;
            return 0;
        }
        struct pollfd pfd[2] = {{outp[0], POLLIN, 0}, {errp[0], POLLIN, 0}};
        poll(pfd, 2, 100);
        char tmp[2048];
        ssize_t k;
        for (int reads = 0; reads < 64 && (k = read(outp[0], tmp, sizeof(tmp))) > 0; reads++) {
            if (stream && send_chunk(job_id, "out", tmp, (size_t)k) != 0) out_clipped = 1;
            if (o + (size_t)k >= ocap) { out_clipped = 1; k = (ssize_t)(ocap - 1 - o); }
            if (k > 0 && !stream) {
                memcpy(out + o, tmp, (size_t)k);
                o += (size_t)k;
                out[o] = 0;
            }
        }
        for (int reads = 0; reads < 64 && (k = read(errp[0], tmp, sizeof(tmp))) > 0; reads++) {
            if (stream && send_chunk(job_id, "err", tmp, (size_t)k) != 0) err_clipped = 1;
            if (e + (size_t)k >= ecap) { err_clipped = 1; k = (ssize_t)(ecap - 1 - e); }
            if (k > 0 && !stream) {
                memcpy(errb + e, tmp, (size_t)k);
                e += (size_t)k;
                errb[e] = 0;
            }
        }
        if (done) { done = 2; continue; } /* final bounded drain after reap */
        pthread_mutex_lock(&g_job_mu);
        pid_t w = waitpid(pid, &status, WNOHANG);
        if (w == pid && job) job->pid = 0; /* do not signal a re-used pid after reap */
        pthread_mutex_unlock(&g_job_mu);
        if (w == pid) done = 1;
    }
    close(outp[0]);
    close(errp[0]);
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
        strcpy(out + end, mark);
    }
    if (err_clipped && ecap > marklen + 1) {
        size_t end = ecap - marklen - 1;
        while (end && ((unsigned char)errb[end] & 0xc0) == 0x80) end--;
        strcpy(errb + end, mark);
    }
    if (WIFEXITED(status)) *code = WEXITSTATUS(status);
    else *code = 1;
    if (timed_out) *timed_out = by_timeout;
    return 0;
}
