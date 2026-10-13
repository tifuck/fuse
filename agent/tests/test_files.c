/* Exercise the real connector functions, without starting a connector. */
#include "../src/agent.h"
#include "../src/edit.h"
#include "../src/files.h"
#include "../src/jobs.h"
#include "../src/net.h"
#include "../src/outq.h"
#include "../src/update.h"
#include <assert.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include "../src/config.h"

int main(void) {
    char dir[] = "/tmp/opencode/fuse-files-XXXXXX";
    assert(mkdtemp(dir));
    char path[PATH_MAX], link[PATH_MAX], out[65536];
    snprintf(path, sizeof(path), "%s/file", dir);
    snprintf(link, sizeof(link), "%s/link", dir);
    assert(write_file_atomic(path, "alpha\r\nbeta\r\n") == 0);
    assert(chmod(path, 0751) == 0);
    assert(write_file_atomic(path, "changed\n") == 0);
    struct stat st;
    assert(stat(path, &st) == 0 && (st.st_mode & 0777) == 0751);
    assert(symlink(path, link) == 0);
    assert(write_file_atomic(link, "wrong") != 0);
    assert(read_file_slice(path, 1, 10, out, sizeof(out)) == 0);
    assert(strcmp(out, "changed\n") == 0);
    assert(read_file_slice(dir, 1, 10, out, sizeof(out)) != 0);
    FILE *f = fopen(path, "wb");
    assert(f);
    for (int i = 0; i < 20000; i++) assert(fputs("prefix\n", f) >= 0);
    assert(fputs("wanted\n", f) >= 0);
    assert(fclose(f) == 0);
    assert(read_file_slice(path, 20001, 1, out, sizeof(out)) == 0);
    assert(strcmp(out, "wanted\n") == 0);
    /* Byte clipping must be an error, never successful partial file text. */
    assert(read_file_slice(path, 1, 400, out, 10) != 0);
    assert(out[0] == 0);
    char small[64];
    assert(read_file_slice(path, 1, 400, small, sizeof(small)) != 0);
    assert(errno == EFBIG);
    f = fopen(path, "wb");
    assert(f);
    assert(fwrite("a\0b", 1, 3, f) == 3);
    assert(fclose(f) == 0);
    char *whole = NULL;
    size_t len = 0;
    assert(load_whole(path, &whole, &len) != 0);
    assert(read_file_slice(path, 1, 10, out, sizeof(out)) != 0);
    /* File jobs release their reservation, including cancelled queued writes. */
    cJSON *msg = cJSON_CreateObject();
    cJSON_AddStringToObject(msg, "id", "filejob");
    cJSON_AddStringToObject(msg, "op", "write");
    cJSON_AddStringToObject(msg, "path", path);
    cJSON_AddStringToObject(msg, "content", "saved");
    handle_job(NULL, msg);
    for (int i = 0; i < MAX_JOBS; i++) assert(g_jobs[i].id[0] == 0);
    struct job *cancelled = job_reserve("filejob");
    cancelled->cancel = 1;
    cJSON_ReplaceItemInObject(msg, "content", cJSON_CreateString("wrong"));
    handle_job(NULL, msg);
    assert(read_file_slice(path, 1, 10, out, sizeof(out)) == 0);
    assert(strcmp(out, "saved") == 0);
    for (int i = 0; i < MAX_JOBS; i++) assert(g_jobs[i].id[0] == 0);
    cJSON_Delete(msg);
    /* Intake refuses a live duplicate id instead of sharing its slot. */
    assert(job_admit("dupjob") == 1);
    assert(job_admit("dupjob") == 0);
    struct job *dup = job_reserve("dupjob");
    memset(dup, 0, sizeof(*dup));
    assert(job_admit("dupjob") == 1);
    memset(job_reserve("dupjob"), 0, sizeof(struct job));
    /* replace op: LF-only old_string against a CRLF file is converted, not failed. */
    cJSON *rep = cJSON_CreateObject();
    cJSON_AddStringToObject(rep, "id", "repl1");
    cJSON_AddStringToObject(rep, "op", "replace");
    cJSON_AddStringToObject(rep, "path", path);
    cJSON_AddStringToObject(rep, "old", "saved");
    cJSON_AddStringToObject(rep, "new", "stored");
    handle_job(NULL, rep);
    for (int i = 0; i < MAX_JOBS; i++) assert(g_jobs[i].id[0] == 0);
    assert(read_file_slice(path, 1, 10, out, sizeof(out)) == 0);
    assert(strcmp(out, "stored") == 0);
    cJSON_Delete(rep);
    assert(write_file_atomic(path, "alpha\r\nbeta\r\n") == 0);
    rep = cJSON_CreateObject();
    cJSON_AddStringToObject(rep, "id", "repl2");
    cJSON_AddStringToObject(rep, "op", "replace");
    cJSON_AddStringToObject(rep, "path", path);
    cJSON_AddStringToObject(rep, "old", "alpha\nbeta\n");
    cJSON_AddStringToObject(rep, "new", "one\ntwo\n");
    handle_job(NULL, rep);
    for (int i = 0; i < MAX_JOBS; i++) assert(g_jobs[i].id[0] == 0);
    assert(read_file_slice(path, 1, 10, out, sizeof(out)) == 0);
    assert(strcmp(out, "one\r\ntwo\r\n") == 0);
    cJSON_Delete(rep);
    /* crlf_expand: deterministic LF -> CRLF, embedded CR untouched. */
    {
        char *e = crlf_expand("a\nb\r\nc", 6);
        assert(e && strcmp(e, "a\r\nb\r\nc") == 0);
        free(e);
    }
    /* Oversize file: load_whole reports EFBIG so the replace op can say why. */
    f = fopen(path, "wb");
    assert(f);
    {
        char blk[65536];
        memset(blk, 'x', sizeof(blk));
        long left = EDIT_FILE_MAX + 1;
        while (left > 0) {
            long n = left < (long)sizeof(blk) ? left : (long)sizeof(blk);
            assert(fwrite(blk, 1, (size_t)n, f) == (size_t)n);
            left -= n;
        }
    }
    assert(fclose(f) == 0);
    whole = NULL;
    len = 0;
    assert(load_whole(path, &whole, &len) != 0);
    assert(errno == EFBIG);
    /* A file inside the edit ceiling loads whole and is editable end to end. */
    f = fopen(path, "wb");
    assert(f);
    for (int i = 0; i < 400001; i++) assert(fputc('y', f) != EOF);
    assert(fclose(f) == 0);
    assert(load_whole(path, &whole, &len) == 0);
    assert(len == 400001);
    free(whole);
    assert(write_file_atomic(path, "final\n") == 0);
    /* H1: a failed flush (no live socket) must leave frames queued. */
    int queued = g_out_n;
    assert(out_enqueue(1, (const unsigned char *)"abc", 3) == 0);
    assert(out_enqueue(1, (const unsigned char *)"de", 2) == 0);
    assert(out_flush(NULL) == -1);
    assert(g_out_n == queued + 2);
    /* Update gate: strictly newer versions only. */
    assert(ver_newer("1.2.1", "1.2.0") == 1);
    assert(ver_newer("2.0", "1.9.9") == 1);
    assert(ver_newer("1.2.0", "1.2.0") == 0);
    assert(ver_newer("1.1.9", "1.2.0") == 0);
    assert(ver_newer("garbage", "1.0.0") == 0);
    /* A served binary identical to the running one is not an update. */
    {
        char self[PATH_MAX];
        snprintf(self, sizeof(self), "%s/self", dir);
        assert(write_file_atomic(self, "abc") == 0);
        assert(update_is_self(self, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
        assert(!update_is_self(self, "0000000000000000000000000000000000000000000000000000000000000000"));
        assert(!update_is_self("/nonexistent/fuse-agent", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
        assert(unlink(self) == 0);
    }
    /* b64 must refuse undersized output buffers. */
    {
        unsigned char bin[3] = {1, 2, 3};
        char b64ok[8];
        char b64small[4];
        assert(b64(bin, 3, b64ok, sizeof(b64ok)) == 0);
        assert(b64(bin, 3, b64small, sizeof(b64small)) == -1);
    }
    /* Truncation tracks output clipping, not file content: a file that
     * mentions the marker text must still read back as complete. */
    f = fopen(path, "wb");
    assert(f);
    assert(fputs("int clipped = (out && strstr(out, \"[fuse-agent: output truncated]\")) ||\n", f) >= 0);
    assert(fclose(f) == 0);
    {
        cJSON *rd = cJSON_CreateObject();
        cJSON_AddStringToObject(rd, "id", "rdjob");
        cJSON_AddStringToObject(rd, "op", "read");
        cJSON_AddStringToObject(rd, "path", path);
        cJSON_AddNumberToObject(rd, "offset", 1);
        cJSON_AddNumberToObject(rd, "limit", 10);
        int before = g_out_n;
        handle_job(NULL, rd);
        cJSON_Delete(rd);
        assert(g_out_n == before + 1);
        int last = (g_out_tail + OUT_QMAX - 1) % OUT_QMAX;
        char frame[4096];
        assert(g_outq[last].n < sizeof(frame));
        memcpy(frame, g_outq[last].data, g_outq[last].n);
        frame[g_outq[last].n] = 0;
        cJSON *res = cJSON_Parse(frame);
        assert(res);
        assert(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(res, "ok")));
        assert(cJSON_IsFalse(cJSON_GetObjectItemCaseSensitive(res, "truncated")));
        cJSON_Delete(res);
    }
    /* A lone control frame must be handed back to the session loop. Reading
     * on for a next frame blocks for the I/O timeout and drops the session. */
    {
        struct tls t;
        memset(&t, 0, sizeof(t));
        t.fd = -1;
        unsigned char frame[] = {0x89, 0x03, 'a', 'b', 'c'};
        memcpy(t.pb, frame, sizeof(frame));
        t.pb_len = sizeof(frame);
        t.pb_off = 0;
        int op = 0;
        size_t n = 0;
        char *msg = ws_read_text(&t, &op, &n);
        assert(msg);
        assert(op == 9);
        assert(n == 3);
        assert(memcmp(msg, "abc", 3) == 0);
        free(msg);
    }
    /* A FIFO with no writer is refused at once, never blocks a worker. */
    {
        char fifo[PATH_MAX];
        snprintf(fifo, sizeof(fifo), "%s/fifo", dir);
        assert(mkfifo(fifo, 0600) == 0);
        assert(read_file_slice(fifo, 1, 10, out, sizeof(out)) != 0);
        char *buf = NULL;
        size_t blen = 0;
        assert(load_whole(fifo, &buf, &blen) != 0);
        assert(unlink(fifo) == 0);
    }
    /* Output bytes become valid UTF-8 JSON: good sequences pass, a stray
     * byte becomes U+FFFD, and a split character is held for the next read. */
    {
        const char raw[] = "ok \xc3\xa9 \xff \xe2\x82";
        cJSON *s = json_string_bytes(raw, sizeof(raw) - 1);
        assert(s && strcmp(s->valuestring, "\"ok \xc3\xa9 \\ufffd \\ufffd\\ufffd\"") == 0);
        cJSON_Delete(s);
        assert(utf8_incomplete_tail("ab\xe2\x82", 4) == 2);
        assert(utf8_incomplete_tail("ab\xe2\x82\xac", 5) == 0);
        assert(utf8_incomplete_tail("ab\xf0", 3) == 1);
        assert(utf8_incomplete_tail("abc", 3) == 0);
    }
    /* A failed in-place rewrite (hard-linked file, write past RLIMIT_FSIZE
     * standing in for ENOSPC) puts the old bytes back, not a truncated file;
     * a failed config save leaves the previous config intact. */
    {
        char hard[PATH_MAX], twin[PATH_MAX], conf[PATH_MAX], back[64];
        snprintf(hard, sizeof(hard), "%s/hard", dir);
        snprintf(twin, sizeof(twin), "%s/twin", dir);
        snprintf(conf, sizeof(conf), "%s/agent.conf", dir);
        assert(write_file_atomic(hard, "abc") == 0);
        assert(linkat(AT_FDCWD, hard, AT_FDCWD, twin, 0) == 0); /* `link` is shadowed here */
        strcpy(g_url, "https://fuse.test");
        strcpy(g_token, "fa_token");
        assert(save_config(conf) == 0);
        signal(SIGXFSZ, SIG_IGN);
        struct rlimit old, lim;
        assert(getrlimit(RLIMIT_FSIZE, &old) == 0);
        lim.rlim_cur = 16; /* soft limit only: the hard one could not be raised back */
        lim.rlim_max = old.rlim_max;
        assert(setrlimit(RLIMIT_FSIZE, &lim) == 0);
        assert(write_file_atomic(hard, "a much longer replacement that does not fit") != 0);
        strcpy(g_token, "fa_a_much_longer_replacement_token");
        assert(save_config(conf) != 0);
        assert(setrlimit(RLIMIT_FSIZE, &old) == 0);
        assert(read_file_slice(hard, 1, 10, back, sizeof(back)) == 0 && strcmp(back, "abc") == 0);
        g_token[0] = 0;
        assert(load_config(conf) == 0 && strcmp(g_token, "fa_token") == 0);
        assert(unlink(twin) == 0 && unlink(hard) == 0 && unlink(conf) == 0);
    }
    assert(unlink(link) == 0);
    assert(unlink(path) == 0);
    assert(rmdir(dir) == 0);
    puts("connector file reliability tests passed");
    return 0;
}
