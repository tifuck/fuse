/* Links the connector modules (minus main); does not start a connector. */
#include "../src/agent.h"
#include "../src/files.h"
#include "../src/jobs.h"
#include "../src/outq.h"
#include <assert.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <openssl/evp.h>

int main(void) {
    char dir[] = "/tmp/fuse/binary-XXXXXX";
    assert(mkdtemp(dir));
    char path[PATH_MAX], fifo[PATH_MAX];
    snprintf(path, sizeof(path), "%s/image", dir);
    snprintf(fifo, sizeof(fifo), "%s/fifo", dir);
    int fd = open(path, O_CREAT | O_WRONLY, 0600);
    assert(fd >= 0);
    unsigned char bytes[32769];
    for (int i = 0; i < 32769; i++) bytes[i] = (unsigned char)i;
    assert(write(fd, bytes, sizeof(bytes)) == sizeof(bytes));
    close(fd);
    char *js = read_binary(path, 0, 32768);
    assert(js);
    cJSON *r = cJSON_Parse(js); free(js);
    assert(json_int(r, "size", 0) == 32769 && !json_bool(r, "eof"));
    const char *data = json_cstr(r, "data", "");
    unsigned char decoded[32770];
    assert(EVP_DecodeBlock(decoded, (const unsigned char *)data, strlen(data)) == 32769);
    assert(memcmp(decoded, bytes, 32768) == 0);
    char identity[80]; json_str(r, "identity", identity, sizeof(identity));
    cJSON_Delete(r);
    js = read_binary(path, 32768, 32768); assert(js);
    r = cJSON_Parse(js); free(js);
    assert(json_bool(r, "eof") && !strcmp(json_cstr(r, "data", ""), "AA=="));
    assert(!strcmp(identity, json_cstr(r, "identity", ""))); cJSON_Delete(r);
    assert(!read_binary(path, -1, 1));
    assert(!read_binary(path, 0, 32769));
    assert(!read_binary(path, 0, 0));
    assert(!read_binary(dir, 0, 1));
    assert(mkfifo(fifo, 0600) == 0);
    assert(!read_binary(fifo, 0, 1));
    fd = open(path, O_WRONLY); assert(fd >= 0);
    assert(write(fd, "z", 1) == 1); close(fd);
    js = read_binary(path, 0, 1); assert(js);
    r = cJSON_Parse(js); free(js);
    assert(strcmp(identity, json_cstr(r, "identity", ""))); cJSON_Delete(r);
    assert(truncate(path, BINARY_FILE_MAX + 1) == 0);
    assert(!read_binary(path, 0, 1) && errno == EFBIG);
    assert(truncate(path, 0) == 0);
    js = read_binary(path, 0, 1); assert(js);
    r = cJSON_Parse(js); free(js);
    assert(json_bool(r, "eof") && !strcmp(json_cstr(r, "data", "bad"), ""));
    cJSON_Delete(r);
    cJSON *msg = cJSON_Parse("{\"id\":\"bin\",\"op\":\"read_binary\"}");
    cJSON_AddStringToObject(msg, "path", path);
    handle_job(NULL, msg);
    int last = (g_out_tail + OUT_QMAX - 1) % OUT_QMAX;
    r = cJSON_ParseWithLength((char *)g_outq[last].data, g_outq[last].n);
    assert(r && json_bool(r, "ok")); cJSON_Delete(r);
    cJSON_AddNumberToObject(msg, "offset", 0.5);
    handle_job(NULL, msg);
    last = (g_out_tail + OUT_QMAX - 1) % OUT_QMAX;
    r = cJSON_ParseWithLength((char *)g_outq[last].data, g_outq[last].n);
    assert(r && !json_bool(r, "ok")); cJSON_Delete(r); cJSON_Delete(msg);
    unlink(fifo); unlink(path); rmdir(dir);
    return 0;
}
