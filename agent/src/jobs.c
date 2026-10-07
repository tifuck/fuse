/* Fuse connector: job registry, worker pool, intake queue and op dispatch. */
#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "agent.h"
#include "edit.h"
#include "exec.h"
#include "files.h"
#include "jobs.h"
#include "net.h"
#include "outq.h"

struct job g_jobs[MAX_JOBS];

pthread_mutex_t g_job_mu = PTHREAD_MUTEX_INITIALIZER;

static pthread_cond_t g_job_cv = PTHREAD_COND_INITIALIZER;

static cJSON *g_queue[MAX_QUEUE];

static int g_qhead = 0, g_qtail = 0;
int g_qcount = 0;

static int g_worker_started = 0;

int g_busy = 0;

struct job *job_reserve(const char *id) {
    if (!id || !id[0]) return NULL;
    pthread_mutex_lock(&g_job_mu);
    for (int i = 0; i < MAX_JOBS; i++) {
        if (g_jobs[i].id[0] && strcmp(g_jobs[i].id, id) == 0) {
            pthread_mutex_unlock(&g_job_mu);
            return &g_jobs[i];
        }
    }
    for (int i = 0; i < MAX_JOBS; i++) {
        if (!g_jobs[i].id[0]) {
            memset(&g_jobs[i], 0, sizeof(g_jobs[i]));
            strncpy(g_jobs[i].id, id, sizeof(g_jobs[i].id) - 1);
            pthread_mutex_unlock(&g_job_mu);
            return &g_jobs[i];
        }
    }
    pthread_mutex_unlock(&g_job_mu);
    return NULL;
}

static void job_clear(struct job *j) {
    if (!j) return;
    pthread_mutex_lock(&g_job_mu);
    memset(j, 0, sizeof(*j));
    pthread_mutex_unlock(&g_job_mu);
}

int job_cancelled(struct job *j) {
    pthread_mutex_lock(&g_job_mu);
    int cancelled = j && j->cancel;
    pthread_mutex_unlock(&g_job_mu);
    return cancelled;
}

static void *job_worker(void *arg) {
    (void)arg;
    for (;;) {
        pthread_mutex_lock(&g_job_mu);
        while (g_qcount == 0) pthread_cond_wait(&g_job_cv, &g_job_mu);
        cJSON *o = g_queue[g_qhead];
        g_queue[g_qhead] = NULL;
        g_qhead = (g_qhead + 1) % MAX_QUEUE;
        g_qcount--;
        g_busy++;
        pthread_mutex_unlock(&g_job_mu);
        if (o) {
            handle_job(NULL, o);
            cJSON_Delete(o);
        }
        pthread_mutex_lock(&g_job_mu);
        g_busy--;
        pthread_mutex_unlock(&g_job_mu);
    }
    return NULL;
}

static void ensure_workers(void) {
    if (g_worker_started) return;
    for (int i = 0; i < WORKERS; i++) {
        pthread_t th;
        if (pthread_create(&th, NULL, job_worker, NULL) == 0) {
            pthread_detach(th);
            g_worker_started++;
        }
    }
}

/* Takes ownership of `owned` (a parsed job object); parses it exactly once. */
void enqueue_job(struct tls *t, cJSON *owned) {
    ensure_workers();
    pthread_mutex_lock(&g_job_mu);
    if (g_qcount >= MAX_QUEUE || g_qcount + g_busy >= MAX_JOBS) {
        pthread_mutex_unlock(&g_job_mu);
        char id[16] = {0};
        if (owned) json_str(owned, "id", id, sizeof(id));
        cJSON_Delete(owned);
        job_clear(job_reserve(id));
        send_res(t, id, 0, 1, "", "", "agent is busy");
        return;
    }
    g_queue[g_qtail] = owned;
    g_qtail = (g_qtail + 1) % MAX_QUEUE;
    g_qcount++;
    pthread_cond_signal(&g_job_cv);
    pthread_mutex_unlock(&g_job_mu);
}

static void handle_job_impl(struct tls *t, cJSON *msg);

void handle_job(struct tls *t, cJSON *msg) {
    static pthread_mutex_t mutation_mu = PTHREAD_MUTEX_INITIALIZER;
    const char *op = json_cstr(msg, "op", "");
    int mutation = strcmp(op, "write") == 0 || strcmp(op, "replace") == 0;
    if (mutation) pthread_mutex_lock(&mutation_mu);
    const char *id = json_cstr(msg, "id", "");
    struct job *job = job_reserve(id);
    pthread_mutex_lock(&g_job_mu);
    int cancelled = job && job->cancel;
    pthread_mutex_unlock(&g_job_mu);
    if (!job) send_res(t, id, 0, 1, "", "", "agent is busy");
    else if (cancelled) send_res(t, id, 0, 1, "", "", "cancelled");
    else handle_job_impl(t, msg);
    /* Every operation reserves a slot, not just exec. */
    job_clear(job);
    if (mutation) pthread_mutex_unlock(&mutation_mu);
}

static void handle_job_impl(struct tls *t, cJSON *msg) {
    char id[16] = {0};
    json_str(msg, "id", id, sizeof(id));
    const char *op = json_cstr(msg, "op", "");
    char out[OUT_CAP];
    char err[OUT_CAP];
    out[0] = err[0] = 0;
    int code = 0;
    if (strcmp(op, "cancel") == 0) return;
    if (strcmp(op, "exec") == 0 || strcmp(op, "exec_stream") == 0) {
        int stream = strcmp(op, "exec_stream") == 0;
        const char *cwd = json_cstr(msg, "cwd", "~");
        const char *cmd = json_cstr(msg, "cmd", "");
        int timeout = json_int(msg, "timeout_ms", 30000);
        char cwd_exp[PATH_MAX];
        if (expand_home(cwd, cwd_exp, sizeof(cwd_exp)) != 0) {
            send_res(t, id, 0, 127, "", "", "invalid cwd");
            return;
        }
        cwd = cwd_exp;
        struct job *job = job_reserve(id);
        if (job_cancelled(job)) {
            send_res(t, id, 0, 1, "", "", "cancelled");
            return;
        }
        int timed_out = 0, clipped = 0;
        if (run_exec(cwd, cmd, timeout, out, EXEC_CAP, err, EXEC_CAP, &code, job, &timed_out, stream, id, &clipped) != 0) {
            send_res(t, id, 0, 1, "", "", "exec failed");
            return;
        }
        {
            char ebuf[240];
            const char *emsg = "";
            ebuf[0] = 0;
            if (code != 0) {
                const char *src = err[0] ? err : out;
                if (src && src[0]) {
                    strncpy(ebuf, src, sizeof(ebuf) - 1);
                    ebuf[sizeof(ebuf) - 1] = 0;
                    char *nl = strchr(ebuf, '\n');
                    if (nl) *nl = 0;
                    emsg = ebuf;
                } else {
                    snprintf(ebuf, sizeof(ebuf), "exit %d", code);
                    emsg = ebuf;
                }
            }
            if (timed_out) emsg = "timed out";
            send_res_tmo(t, id, code == 0, code, out, err, emsg, timed_out, clipped);
        }
        return;
    }
    if (strcmp(op, "read_binary") == 0) {
        cJSON *off = cJSON_GetObjectItemCaseSensitive(msg, "offset");
        cJSON *lim = cJSON_GetObjectItemCaseSensitive(msg, "limit");
        if ((off && (!cJSON_IsNumber(off) || !(off->valuedouble >= 0 && off->valuedouble <= BINARY_FILE_MAX) || off->valuedouble != off->valueint)) ||
            (lim && (!cJSON_IsNumber(lim) || !(lim->valuedouble >= 1 && lim->valuedouble <= BINARY_CHUNK_MAX) || lim->valuedouble != lim->valueint))) {
            send_res(t, id, 0, 1, "", "", "invalid binary offset or limit");
            return;
        }
        char *js = read_binary(json_cstr(msg, "path", ""), off ? off->valueint : 0, lim ? lim->valueint : BINARY_CHUNK_MAX);
        if (!js) send_res(t, id, 0, 1, "", strerror(errno), "read_binary failed");
        else {
            send_res(t, id, 1, 0, js, "", "");
            free(js);
        }
        return;
    }
    if (strcmp(op, "read") == 0) {
        const char *path = json_cstr(msg, "path", "");
        if (read_file_slice(path, json_int(msg, "offset", 1), json_int(msg, "limit", 200), out, sizeof(out)) != 0) {
            const char *why;
            if (errno == EINVAL)
                why = "file is not UTF-8 text (contains NUL bytes)";
            else if (errno == EFBIG)
                why = "read output exceeds 65536 bytes; use a smaller limit or grep";
            else if (errno == EOVERFLOW)
                why = "read scan exceeds 64 MiB; narrow the offset or use grep";
            else
                why = strerror(errno);
            send_res(t, id, 0, 1, "", why, "read failed");
        }
        else
            send_res(t, id, 1, 0, out, "", "");
        return;
    }
    if (strcmp(op, "write") == 0) {
        const char *path = json_cstr(msg, "path", "");
        const char *content = json_cstr(msg, "content", "");
        if (write_file_atomic(path, content) != 0) {
            char why[256];
            if (errno == EFBIG)
                snprintf(why, sizeof(why), "content exceeds %ld bytes; split the write", WRITE_FILE_MAX);
            else if (errno == EINVAL)
                snprintf(why, sizeof(why), "write target must be a regular file");
            else
                snprintf(why, sizeof(why), "write failed: %s", strerror(errno));
            send_res(t, id, 0, 1, "", why, "write failed");
        }
        else send_res(t, id, 1, 0, "wrote", "", "");
        return;
    }
    if (strcmp(op, "list") == 0) {
        if (list_dir(json_cstr(msg, "path", "."), out, sizeof(out)) != 0) {
            char why[256];
            snprintf(why, sizeof(why), "list failed: %s", strerror(errno));
            send_res(t, id, 0, 1, "", why, "list failed");
        }
        else send_res(t, id, 1, 0, out, "", "");
        return;
    }
    if (strcmp(op, "browse") == 0) {
        char *js = browse_json(json_cstr(msg, "path", "~"));
        if (!js) send_res(t, id, 0, 1, "", "", "browse failed");
        else {
            send_res(t, id, 1, 0, js, "", "");
            free(js);
        }
        return;
    }
    if (strcmp(op, "grep") == 0 || strcmp(op, "glob") == 0) {
        /* The server runs Linux searches as exec commands (rg, or a git/find
         * listing + grep) so they share one implementation with SSH hosts.
         * A native copy here drifted (20 matches per file, 200-char
         * patterns); refuse clearly instead of answering differently. */
        send_res(t, id, 0, 1, "", "", "native grep/glob is not supported on Linux connectors; use exec");
        return;
    }
    if (strcmp(op, "replace") == 0) {
        const char *path = json_cstr(msg, "path", "");
        char *buf = NULL;
        size_t len = 0;
        char expanded[PATH_MAX];
        struct stat before, after;
        if (expand_home(path, expanded, sizeof(expanded)) != 0) {
            send_res(t, id, 0, 1, "", "", "invalid path");
            return;
        }
        if (lstat(expanded, &before) != 0) {
            send_res(t, id, 0, 1, "", "", errno == ENOENT ? "file does not exist" : "edit target is not readable");
            return;
        }
        if (!S_ISREG(before.st_mode)) {
            send_res(t, id, 0, 1, "", "", "edit target must be a regular file");
            return;
        }
        /* One edit (old/new/replace_all) or an ordered "edits" array applied
         * to the same in-memory text and published once: all or nothing. */
        cJSON *edits = cJSON_GetObjectItemCaseSensitive(msg, "edits");
        int nedits = cJSON_IsArray(edits) ? cJSON_GetArraySize(edits) : 1;
        if (nedits < 1 || nedits > 100) {
            send_res(t, id, 0, 1, "", "", "edits must contain 1 to 100 items");
            return;
        }
        if (load_whole(path, &buf, &len) != 0) {
            char why[288];
            if (errno == EFBIG)
                snprintf(why, sizeof(why),
                         "file too large to edit (maximum %ld bytes); apply the change with run_terminal_command",
                         EDIT_FILE_MAX);
            else if (errno == EINVAL)
                snprintf(why, sizeof(why), "file is not UTF-8 text (contains NUL bytes)");
            else
                snprintf(why, sizeof(why), "read failed: %s", strerror(errno));
            send_res(t, id, 0, 1, "", why, "replace failed");
            return;
        }
        long total = 0;
        for (int i = 0; i < nedits; i++) {
            cJSON *item = cJSON_IsArray(edits) ? cJSON_GetArrayItem(edits, i) : msg;
            const char *old = json_cstr(item, cJSON_IsArray(edits) ? "old_string" : "old", "");
            const char *newv = json_cstr(item, cJSON_IsArray(edits) ? "new_string" : "new", "");
            int all = json_bool(item, "replace_all");
            char why[320];
            long reps = 0;
            if (apply_edit(&buf, &len, old, newv, all, &reps, why, sizeof(why)) != 0) {
                free(buf);
                char msgbuf[400];
                if (cJSON_IsArray(edits))
                    snprintf(msgbuf, sizeof(msgbuf), "edit %d of %d: %s; no edits were applied", i + 1, nedits, why);
                else
                    snprintf(msgbuf, sizeof(msgbuf), "%s", why);
                send_res(t, id, 0, 1, "", "", msgbuf);
                return;
            }
            total += reps;
        }
        if (lstat(expanded, &after) != 0 || before.st_dev != after.st_dev ||
            before.st_ino != after.st_ino || before.st_size != after.st_size ||
            before.st_mtim.tv_sec != after.st_mtim.tv_sec || before.st_mtim.tv_nsec != after.st_mtim.tv_nsec) {
            free(buf);
            send_res(t, id, 0, 1, "", "", "file changed during edit; read it again");
            return;
        }
        int wr = write_file_atomic_limit(path, buf, EDIT_FILE_MAX);
        free(buf);
        if (wr != 0) {
            char why[288];
            if (errno == EFBIG)
                snprintf(why, sizeof(why), "replacement too large (maximum %ld bytes)", EDIT_FILE_MAX);
            else
                snprintf(why, sizeof(why), "write failed: %s", strerror(errno));
            send_res(t, id, 0, 1, "", why, "replace failed");
        }
        else {
            snprintf(out, sizeof(out), "%ld replacement(s)", total);
            send_res(t, id, 1, 0, out, "", "");
        }
        return;
    }
    if (strcmp(op, "kill") == 0) {
        const char *kid = json_cstr(msg, "id", "");
        pthread_mutex_lock(&g_job_mu);
        for (int i = 0; i < MAX_JOBS; i++) {
            if (g_jobs[i].id[0] && strcmp(g_jobs[i].id, kid) == 0) {
                g_jobs[i].cancel = 1;
                if (g_jobs[i].pid > 0) kill(-g_jobs[i].pid, SIGKILL);
            }
        }
        pthread_mutex_unlock(&g_job_mu);
        send_res(t, id, 1, 0, "cancelled", "", "");
        return;
    }
    send_res(t, id, 0, 1, "", "", "unknown op");
}

/* No queued results and no running/queued jobs: safe to execv() into an
 * update without orphaning in-flight work. */
int agent_idle(void) {
    /* Order matters: a worker enqueues its result (g_out_mu) strictly before
     * dropping g_busy (g_job_mu). Sampling jobs first, then outputs, cannot
     * miss work: jobs==0 implies every completed job is already counted in
     * g_out_n. The previous outputs-first order could observe out_n==0, then
     * a completion enqueued, then jobs==0, and report idle while a result was
     * still queued - and maybe_update's execv would discard it. */
    pthread_mutex_lock(&g_job_mu);
    int jobs = g_qcount + g_busy;
    pthread_mutex_unlock(&g_job_mu);
    pthread_mutex_lock(&g_out_mu);
    int out_n = g_out_n;
    pthread_mutex_unlock(&g_out_mu);
    return out_n == 0 && jobs == 0;
}
