#ifndef FUSE_NET_H
#define FUSE_NET_H

/* Outbound TLS + WebSocket transport: URL parsing, connect, framing. */

#include <openssl/ssl.h>

struct url {
    char host[256];
    char path[256];
    int port;
};
struct tls {
    int fd;
    SSL_CTX *ctx;
    SSL *ssl;
    /* Bytes read past the end of the HTTP handshake header, stashed so the
     * first WebSocket frame is not lost if the server pipelined them. */
    unsigned char pb[512];
    size_t pb_len;
    size_t pb_off;
};

int parse_url(const char *raw, struct url *u);
int tls_connect(struct tls *t, const struct url *u);
void tls_close(struct tls *t);
int tls_write_all(struct tls *t, const void *buf, size_t n);
int tls_read_some(struct tls *t, void *buf, size_t n);
int b64(const unsigned char *in, int inlen, char *out, int outcap);
size_t ws_header(int opcode, const unsigned char *mask, size_t n, unsigned char *hdr);
int ws_handshake(struct tls *t, const struct url *u);
int ws_send(struct tls *t, int opcode, const unsigned char *data, size_t n);
int ws_send_text(struct tls *t, const char *s);
char *ws_read_text(struct tls *t, int *opcode_out, size_t *n_out);

#endif
