/* The updater's HTTP response reader: Content-Length, chunked (a proxy may
 * chunk; the old reader wrote the chunk framing into the binary), HTTP/1.0,
 * read-to-close, and refusals. Fed a few bytes per read to hit every
 * boundary. */
#include "../src/agent.h"
#include "../src/update.h"
#include <assert.h>

struct src {
    const char *p;
    size_t left, step;
};

static int rd(void *ctx, char *buf, size_t n) {
    struct src *s = ctx;
    size_t k = s->left < s->step ? s->left : s->step;
    if (k > n) k = n;
    memcpy(buf, s->p, k);
    s->p += k;
    s->left -= k;
    return (int)k;
}

/* Body on success, NULL on refusal. */
static char *get(const char *resp, size_t max) {
    static char out[4096];
    for (size_t step = 1; step <= 7; step += 3) {
        struct src s = {resp, strlen(resp), step};
        FILE *f = fmemopen(out, sizeof(out), "w");
        int rc = http_read_response(rd, &s, f, max);
        long n = ftell(f);
        fclose(f);
        if (rc != 0) return NULL;
        out[n] = 0;
    }
    return out;
}

int main(void) {
    assert(!strcmp(get("HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello", 100), "hello"));
    assert(!strcmp(get("HTTP/1.1 200 OK\r\ntransfer-encoding: chunked\r\n\r\n"
                       "5\r\nhello\r\n7;ext=1\r\n, world\r\n0\r\nX-Trailer: y\r\n\r\n", 100),
                   "hello, world"));
    assert(!strcmp(get("HTTP/1.0 200 OK\r\n\r\nto close", 100), "to close"));
    assert(!get("HTTP/1.1 404 Not Found\r\nContent-Length: 2\r\n\r\nno", 100));
    assert(!get("HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\nshort", 100));       /* truncated */
    assert(!get("HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello", 4));         /* too big */
    assert(!get("HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\nshort", 100));      /* negative */
    assert(!get("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n", 100)); /* no last chunk */
    assert(!get("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n9\r\nhello\r\n0\r\n\r\n", 100));
    assert(!get("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n10\r\n0123456789abcdef\r\n0\r\n\r\n", 8));
    assert(!get("HTTP/1.1 200 OK\r\n\r\nmore than four", 4));
    puts("test_http: OK");
    return 0;
}
