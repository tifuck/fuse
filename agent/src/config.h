#ifndef FUSE_CONFIG_H
#define FUSE_CONFIG_H

/* Pairing credentials and the agent.conf file they persist to. */

#include <limits.h>
#include <stddef.h>

extern char g_url[256];
extern char g_token[192];
extern char g_code[128];
extern char g_config[PATH_MAX];
extern int g_auto_update;

int load_config(const char *path);
int save_config(const char *path);
void default_config_path(char *dst, size_t cap, int system_wide);
void ensure_home_env(void);
void hostname_user(char *host, size_t hcap, char *user, size_t ucap);
const char *arch_sku(void);

#endif
