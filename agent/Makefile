VERSION := $(shell cat VERSION)
CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE
CPPFLAGS += -Ithird_party -Isrc -DFUSE_AGENT_VERSION=\"$(VERSION)\"
LIBS ?= -lssl -lcrypto -lpthread -ldl
SRC := src/fuse-agent.c src/reconnect.c third_party/cJSON.c
ARCH := $(shell uname -m)
ifeq ($(ARCH),x86_64)
SKU := linux-amd64
else ifeq ($(ARCH),amd64)
SKU := linux-amd64
else ifeq ($(ARCH),aarch64)
SKU := linux-arm64
else ifeq ($(ARCH),arm64)
SKU := linux-arm64
else
SKU := linux-$(ARCH)
endif
BIN := dist/fuse-agent-$(SKU)

# Cross-build for linux-arm64. Requires gcc-aarch64-linux-gnu plus an
# aarch64 OpenSSL build; static crypto so the target only needs glibc.
#   make OPENSSL_ARM64=/path/to/ssl-arm64 linux-arm64
ARM64_CC ?= aarch64-linux-gnu-gcc
OPENSSL_ARM64 ?= /tmp/opencode/ssl-arm64

.PHONY: all dist clean test windows linux-arm64

all: $(BIN)

$(BIN): $(SRC) src/reconnect.h third_party/cJSON.h VERSION
	mkdir -p dist
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ $(SRC) $(LIBS)
	chmod 755 $@

dist: all linux-arm64 windows

linux-arm64:
	mkdir -p dist
	$(ARM64_CC) $(CFLAGS) $(CPPFLAGS) -I$(OPENSSL_ARM64)/include \
	  -o dist/fuse-agent-linux-arm64 $(SRC) \
	  $(OPENSSL_ARM64)/lib/libssl.a $(OPENSSL_ARM64)/lib/libcrypto.a -lpthread -ldl
	chmod 755 dist/fuse-agent-linux-arm64

windows:
	$(MAKE) -C windows dist

tests/test_expand_home: tests/test_expand_home.c
	$(CC) $(CFLAGS) -o $@ $<

tests/test_reconnect: tests/test_reconnect.c src/reconnect.c src/reconnect.h
	$(CC) $(CFLAGS) -Isrc -o $@ tests/test_reconnect.c src/reconnect.c

tests/test_files: tests/test_files.c $(SRC) src/reconnect.h
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ tests/test_files.c src/reconnect.c third_party/cJSON.c $(LIBS)

tests/test_lifecycle: tests/test_lifecycle.c $(SRC) src/reconnect.h
	$(CC) $(CFLAGS) $(CPPFLAGS) -Isrc -o $@ tests/test_lifecycle.c src/reconnect.c third_party/cJSON.c $(LIBS)

test: tests/test_expand_home tests/test_reconnect tests/test_files tests/test_lifecycle
	tests/test_expand_home
	tests/test_reconnect
	tests/test_files
	tests/test_lifecycle

clean:
	rm -f dist/fuse-agent-* tests/test_expand_home tests/test_reconnect tests/test_files
