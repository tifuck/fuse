#ifndef FUSE_JOBS_H
#define FUSE_JOBS_H

/* Job registry (cancel + pid), worker pool, intake queue, op dispatch. */

#include <pthread.h>
#include <signal.h>
#include <sys/types.h>

#include "agent.h"

struct job {
    char id[16];
    pid_t pid;
    volatile sig_atomic_t cancel;
};

extern struct job g_jobs[];
extern pthread_mutex_t g_job_mu;
extern int g_busy;
extern int g_qcount;

struct tls;

struct job *job_reserve(const char *id);
int job_admit(const char *id);
int job_cancelled(struct job *j);
void enqueue_job(struct tls *t, cJSON *owned);
void handle_job(struct tls *t, cJSON *msg);
int agent_idle(void);

#endif
