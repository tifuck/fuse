VERSION := $(shell cat ../VERSION)
GO ?= go
export CGO_ENABLED=0
LDFLAGS = -H=windowsgui -s -w -X main.version=$(VERSION)
DIST := ../dist

.PHONY: test dist clean

test:
	$(GO) test ./...

dist:
	mkdir -p $(DIST)
	GOOS=windows GOARCH=amd64 $(GO) build -ldflags "$(LDFLAGS)" -o $(DIST)/fuse-agent-windows-amd64.exe .
	GOOS=windows GOARCH=arm64 $(GO) build -ldflags "$(LDFLAGS)" -o $(DIST)/fuse-agent-windows-arm64.exe .

clean:
	rm -f $(DIST)/fuse-agent-windows-*.exe
