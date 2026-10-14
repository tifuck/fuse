VERSION := $(shell cat VERSION)
CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE
CPPFLAGS += -Ithird_party -Isrc -DFUSE_AGENT_VERSION=\"$(VERSION)\"
LIBS ?= -lssl -lcrypto -lpthread -ldl
SRC := src/main.c src/config.c src/lifecycle.c src/net.c src/outq.c src/exec.c \
       src/files.c src/edit.c src/jobs.c src/update.c src/session.c \
       src/reconnect.c third_party/cJSON.c
LIB_SRC := $(filter-out src/main.c,$(SRC))
HDRS := $(wildcard src/*.h) third_party/cJSON.h
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

$(BIN): $(SRC) $(HDRS) VERSION
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

tests/test_files: tests/test_files.c $(LIB_SRC) $(HDRS)
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ tests/test_files.c $(LIB_SRC) $(LIBS)

tests/test_lifecycle: tests/test_lifecycle.c $(LIB_SRC) $(HDRS)
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ tests/test_lifecycle.c $(LIB_SRC) $(LIBS)

tests/test_output_queue: tests/test_output_queue.c $(LIB_SRC) $(HDRS)
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ tests/test_output_queue.c $(LIB_SRC) $(LIBS)

tests/test_binary: tests/test_binary.c $(LIB_SRC) $(HDRS)
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ tests/test_binary.c $(LIB_SRC) $(LIBS)

tests/test_ws_frame: tests/test_ws_frame.c $(LIB_SRC) $(HDRS)
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ tests/test_ws_frame.c $(LIB_SRC) $(LIBS)

tests/test_exec_stream: tests/test_exec_stream.c $(LIB_SRC) $(HDRS)
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ tests/test_exec_stream.c $(LIB_SRC) $(LIBS)

tests/test_jobs_pool: tests/test_jobs_pool.c $(LIB_SRC) $(HDRS)
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ tests/test_jobs_pool.c $(LIB_SRC) $(LIBS)

tests/test_ws_tls: tests/test_ws_tls.c $(LIB_SRC) $(HDRS)
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ tests/test_ws_tls.c $(LIB_SRC) $(LIBS)

tests/test_http: tests/test_http.c $(LIB_SRC) $(HDRS)
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ tests/test_http.c $(LIB_SRC) $(LIBS)

test: tests/test_expand_home tests/test_reconnect tests/test_files tests/test_lifecycle tests/test_output_queue tests/test_binary tests/test_ws_frame tests/test_exec_stream tests/test_jobs_pool tests/test_ws_tls tests/test_http
	tests/test_expand_home
	tests/test_reconnect
	tests/test_files
	tests/test_lifecycle
	tests/test_output_queue
	tests/test_binary
	tests/test_ws_frame
	tests/test_exec_stream
	tests/test_jobs_pool
	tests/test_ws_tls
	tests/test_http

clean:
	rm -f dist/fuse-agent-* tests/test_expand_home tests/test_reconnect tests/test_files tests/test_lifecycle \
	  tests/test_output_queue tests/test_binary tests/test_ws_frame tests/test_exec_stream tests/test_jobs_pool tests/test_ws_tls tests/test_http
