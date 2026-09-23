#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Duplicate of fuse-agent expand_home for a focused unit test. */
static int expand_home(const char *path, char *out, size_t cap, const char *home) {
    if (!path || !path[0]) path = "~";
    const char *rest = NULL;
    if (path[0] == '~') rest = path + 1;
    else if (strncmp(path, "$HOME", 5) == 0) rest = path + 5;
    if (!rest) {
        if (strlen(path) >= cap) return -1;
        memcpy(out, path, strlen(path) + 1);
        return 0;
    }
    if (rest[0] && rest[0] != '/') {
        if (strlen(path) >= cap) return -1;
        memcpy(out, path, strlen(path) + 1);
        return 0;
    }
    if (snprintf(out, cap, "%s%s", home, rest) >= (int)cap) return -1;
    return 0;
}

static int fail = 0;

static void expect(const char *got, const char *want, const char *label) {
    if (strcmp(got, want) != 0) {
        fprintf(stderr, "FAIL %s: got %s want %s\n", label, got, want);
        fail = 1;
    }
}

int main(void) {
    char out[256];
    expand_home("$HOME", out, sizeof(out), "/root");
    expect(out, "/root", "$HOME");
    expand_home("~", out, sizeof(out), "/root");
    expect(out, "/root", "~");
    expand_home("", out, sizeof(out), "/root");
    expect(out, "/root", "empty");
    expand_home("$HOME/src", out, sizeof(out), "/root");
    expect(out, "/root/src", "$HOME/src");
    expand_home("~/bin", out, sizeof(out), "/home/user");
    expect(out, "/home/user/bin", "~/bin");
    expand_home("/srv/api", out, sizeof(out), "/root");
    expect(out, "/srv/api", "abs");
    if (fail) return 1;
    puts("ok");
    return 0;
}
