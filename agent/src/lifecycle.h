#ifndef FUSE_LIFECYCLE_H
#define FUSE_LIFECYCLE_H

/* Wake/stop pipes carry signals and cross-thread wakes to the single session
 * loop reader; the watch thread turns resume/network changes into reconnects. */

#include <pthread.h>

#include "agent.h"
#include "reconnect.h"

extern int g_stop_main;    /* main-thread only; set via stop pipe */
extern int g_wake_reason;  /* main-thread only; latest wake reason */
extern pthread_mutex_t g_conn_mu;
extern int g_live_fd;
extern int g_wake_rd, g_wake_wr;
extern int g_main_stop_rd, g_main_stop_wr;

void on_sig(int sig);
void on_usr1(int sig);
void consume_wake(void);
void record_main_stop(void);
int wake_take(void);
void set_live_fd(int fd);
void request_reconnect(int reason);
void init_signal_pipes(void);
void start_watch_thread(void);

#endif
