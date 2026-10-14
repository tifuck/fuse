/* ws_send over real TLS: masked frames round-trip, and a frame is one
 * SSL_write (the fewest TLS records), not a header record plus a record per
 * 4 KB piece. ws_read_text reads a large server frame back. */
#include "../src/agent.h"
#include "../src/net.h"
#include <assert.h>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/x509.h>
#include <pthread.h>
#include <sys/socket.h>

static const size_t sizes[] = {5, 4000, 70000};
static int records = 0;

static void count_records(int write_p, int version, int content_type, const void *buf, size_t len, SSL *ssl, void *arg) {
    (void)version, (void)buf, (void)len, (void)ssl, (void)arg;
    if (!write_p && content_type == SSL3_RT_HEADER) records++;
}

static void read_n(SSL *s, unsigned char *b, size_t n) {
    size_t got = 0;
    while (got < n) {
        int k = SSL_read(s, b + got, (int)(n - got));
        assert(k > 0);
        got += (size_t)k;
    }
}

static void *server(void *arg) {
    SSL *s = arg;
    assert(SSL_accept(s) == 1);
    SSL_set_msg_callback(s, count_records);
    for (size_t f = 0; f < sizeof(sizes) / sizeof(sizes[0]); f++) {
        records = 0;
        unsigned char h[14];
        read_n(s, h, 2);
        assert(h[0] == 0x81 && (h[1] & 0x80));
        size_t n = h[1] & 0x7f, ext = n == 126 ? 2 : n == 127 ? 8 : 0;
        read_n(s, h + 2, ext + 4);
        if (ext) {
            n = 0;
            for (size_t i = 0; i < ext; i++) n = (n << 8) | h[2 + i];
        }
        assert(n == sizes[f]);
        unsigned char *p = malloc(n), *mask = h + 2 + ext;
        read_n(s, p, n);
        for (size_t i = 0; i < n; i++) assert((p[i] ^ mask[i & 3]) == (unsigned char)('a' + i % 26));
        free(p);
        int most = (int)((n + 14 + 16383) / 16384);
        if (records > most) fprintf(stderr, "frame %zu: %d records, want <= %d\n", n, records, most);
        assert(records <= most);
    }
    size_t n = 100000;
    unsigned char *out = malloc(n + 10);
    out[0] = 0x81, out[1] = 127;
    for (int i = 0; i < 8; i++) out[2 + i] = (unsigned char)(n >> (56 - 8 * i));
    for (size_t i = 0; i < n; i++) out[10 + i] = (unsigned char)('A' + i % 26);
    assert(SSL_write(s, out, (int)(n + 10)) == (int)(n + 10));
    free(out);
    return NULL;
}

int main(void) {
    EVP_PKEY *key = EVP_EC_gen("P-256");
    X509 *cert = X509_new();
    X509_set_version(cert, 2);
    ASN1_INTEGER_set(X509_get_serialNumber(cert), 1);
    X509_gmtime_adj(X509_getm_notBefore(cert), 0);
    X509_gmtime_adj(X509_getm_notAfter(cert), 3600);
    X509_set_pubkey(cert, key);
    X509_NAME_add_entry_by_txt(X509_get_subject_name(cert), "CN", MBSTRING_ASC, (unsigned char *)"t", -1, -1, 0);
    X509_set_issuer_name(cert, X509_get_subject_name(cert));
    assert(X509_sign(cert, key, EVP_sha256()));
    SSL_CTX *sctx = SSL_CTX_new(TLS_server_method()), *cctx = SSL_CTX_new(TLS_client_method());
    assert(SSL_CTX_use_certificate(sctx, cert) == 1 && SSL_CTX_use_PrivateKey(sctx, key) == 1);
    int sv[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    SSL *ss = SSL_new(sctx);
    SSL_set_fd(ss, sv[1]);
    pthread_t th;
    assert(pthread_create(&th, NULL, server, ss) == 0);
    struct tls t;
    memset(&t, 0, sizeof(t));
    t.fd = sv[0];
    t.ssl = SSL_new(cctx);
    SSL_set_fd(t.ssl, sv[0]);
    assert(SSL_connect(t.ssl) == 1);
    for (size_t f = 0; f < sizeof(sizes) / sizeof(sizes[0]); f++) {
        unsigned char *p = malloc(sizes[f]);
        for (size_t i = 0; i < sizes[f]; i++) p[i] = (unsigned char)('a' + i % 26);
        assert(ws_send(&t, 1, p, sizes[f]) == 0);
        free(p);
    }
    int op = 0;
    size_t n = 0;
    char *msg = ws_read_text(&t, &op, &n);
    assert(msg && op == 1 && n == 100000 && msg[0] == 'A' && msg[99999] == (char)('A' + 99999 % 26));
    free(msg);
    pthread_join(th, NULL);
    puts("test_ws_tls: OK");
    return 0;
}
