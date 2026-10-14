/* Pins ws_header: a >64 KiB frame must carry the full 64-bit length in
 * header bytes 2..9 with the mask at 10..13 (the pre-fix layout wrote the
 * low length bytes at hdr[10..11] and then overwrote them with the mask). */
#include "../src/agent.h"
#include "../src/net.h"
#include <assert.h>

int main(void) {
    unsigned char mask[4] = {0xaa, 0xbb, 0xcc, 0xdd};
    /* 64-bit form: mask must not clobber the length field. */
    for (size_t n = 65536; n < 1u << 20; n += 8191) {
        unsigned char hdr[14];
        size_t hlen = ws_header(1, mask, n, hdr);
        assert(hlen == 14);
        assert(hdr[0] == (0x80 | 1));
        assert(hdr[1] == (0x80 | 127));
        uint64_t dec = 0;
        for (int i = 2; i < 10; i++) dec = (dec << 8) | hdr[i];
        assert(dec == n);
        assert(hdr[10] == 0xaa && hdr[11] == 0xbb && hdr[12] == 0xcc && hdr[13] == 0xdd);
    }
    /* Boundaries: 65535 uses the 16-bit form, 65536 the 64-bit form. */
    unsigned char hdr[14];
    size_t hlen = ws_header(1, mask, 65535, hdr);
    assert(hlen == 8 && hdr[1] == (0x80 | 126));
    assert((((unsigned)hdr[2] << 8) | hdr[3]) == 65535);
    assert(hdr[4] == 0xaa && hdr[7] == 0xdd);
    hlen = ws_header(1, mask, 65536, hdr);
    assert(hlen == 14 && hdr[1] == (0x80 | 127));
    hlen = ws_header(2, mask, 5, hdr);
    assert(hlen == 6 && hdr[1] == (0x80 | 5) && hdr[2] == 0xaa);
    /* IPv6 literal URLs: the first ':' inside brackets is not the port. */
    struct url u;
    assert(parse_url("https://[::1]:8088/base", &u) == 0);
    assert(strcmp(u.host, "::1") == 0 && u.port == 8088 && strcmp(u.path, "/base") == 0);
    assert(parse_url("https://[fd00::5]/x", &u) == 0);
    assert(strcmp(u.host, "fd00::5") == 0 && u.port == 443);
    assert(parse_url("https://[::1]bad", &u) != 0);
    assert(parse_url("https://host:8443/p", &u) == 0 && u.port == 8443);
    assert(parse_url("https://host", &u) == 0 && u.port == 443);
    /* Host header keeps a non-default port and brackets IPv6; the connect
     * path keeps a base path prefix (it was overwritten with the bare path). */
    char host[300];
    url_host_header(&u, host, sizeof(host));
    assert(!strcmp(host, "host"));
    assert(parse_url("https://[::1]:8088/base/", &u) == 0);
    url_host_header(&u, host, sizeof(host));
    assert(!strcmp(host, "[::1]:8088"));
    assert(url_join_path(&u, "/agent/v1/connect") == 0 && !strcmp(u.path, "/base/agent/v1/connect"));
    assert(parse_url("https://host", &u) == 0 && url_join_path(&u, "/agent/v1/connect") == 0);
    assert(!strcmp(u.path, "/agent/v1/connect"));
    puts("test_ws_frame: OK");
    return 0;
}
