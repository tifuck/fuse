#ifndef FUSE_AGENT_H
#define FUSE_AGENT_H

/* Shared connector internals: policy caps, JSON helpers, fatal error exit.
 * Subsystems live in their own modules (net, outq, exec, files, edit, jobs,
 * config, update, lifecycle, session) and are reached through their own
 * headers; this one holds only what crosses all of them. */

#include <errno.h>
#include <limits.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "cJSON.h"

#ifndef FUSE_AGENT_VERSION
#define FUSE_AGENT_VERSION "0.0.0"
#endif
#define OUT_CAP 65536
/* exec-family output cap: the server clips results to 6000 chars, so
 * capturing more than this is pure waste of RAM and bandwidth. */
#define EXEC_CAP 8192
/* Whole-file edit ceiling for the replace op. Only old/new travel over the
 * socket (never the file), so this is a RAM guard, not a transport limit.
 * Keep in sync with _REPLACE_FILE_MAX in app/workspace.py and editFileMax in
 * agent/windows/fs.go. */
#define EDIT_FILE_MAX (64L * 1024 * 1024)
/* write op content ceiling (server pre-checks 400000; transport-bounded). */
#define WRITE_FILE_MAX 400000L
#define UPDATE_EVERY_MS (60 * 60 * 1000)
#define WS_MAX (1024 * 1024)
#define WS_DRAIN_MAX (16 * 1024 * 1024)
#define MAX_JOBS 128
#define MAX_QUEUE 128
#define WORKERS 16
#define OUT_QMAX 128
static inline void die(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fputs("fuse-agent: ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
    exit(1);
}
/* Like cJSON_CreateString but never silently truncates at the first NUL:
 * command output is arbitrary bytes and cJSON strings are strlen-bounded.
 * Emits a cJSON_Raw node holding a fully-escaped JSON string literal:
 * " -> \", \ -> \\, bytes outside printable/\t\n\r -> \uXXXX. */
static inline cJSON *json_string_bytes(const char *s, size_t n) {
    char *buf = malloc(n * 6 + 3);
    if (!buf) return NULL;
    char *w = buf;
    *w++ = '"';
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') {
            *w++ = '\\';
            *w++ = (char)c;
        } else if (c >= 0x20) {
            *w++ = (char)c;
        } else {
            /* \uXXXX for control bytes (incl. 0x00); \t \n \r stay literal. */
            static const char hex[] = "0123456789abcdef";
            if (c == '\t' || c == '\n' || c == '\r') {
                *w++ = (char)c;
            } else {
                *w++ = '\\'; *w++ = 'u'; *w++ = '0'; *w++ = '0';
                *w++ = hex[c >> 4]; *w++ = hex[c & 15];
            }
        }
    }
    *w++ = '"';
    *w = 0;
    cJSON *o = cJSON_CreateRaw(buf);
    free(buf);
    return o;
}
static inline int json_str(cJSON *o, const char *k, char *dst, size_t cap) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (!cJSON_IsString(v) || !v->valuestring) return -1;
    strncpy(dst, v->valuestring, cap - 1);
    dst[cap - 1] = 0;
    return 0;
}
static inline const char *json_cstr(cJSON *o, const char *k, const char *fallback) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (cJSON_IsString(v) && v->valuestring) return v->valuestring;
    return fallback;
}
static inline int json_int(cJSON *o, const char *k, int fallback) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (cJSON_IsNumber(v)) return v->valueint;
    return fallback;
}
static inline int json_bool(cJSON *o, const char *k) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    return cJSON_IsTrue(v);
}

#endif
