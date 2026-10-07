#ifndef FUSE_SESSION_H
#define FUSE_SESSION_H

/* Connect/hello/session loop; pair_once is the install-time one-shot. */

int session_loop(void);
int pair_once(void);

#endif
