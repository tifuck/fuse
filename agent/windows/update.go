package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type manifest struct {
	Version  string                    `json:"version"`
	Binaries map[string]manifestBinary `json:"binaries"`
}

type manifestBinary struct {
	Path   string `json:"path"`
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
	Size   int64  `json:"size"`
}

func httpGet(url string) ([]byte, error) {
	client := &http.Client{Timeout: 60 * time.Second}
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "fuse-agent/"+version)
	res, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return nil, fmt.Errorf("http %d", res.StatusCode)
	}
	return io.ReadAll(io.LimitReader(res.Body, 80<<20))
}

const updateEvery = time.Hour

func parseVersion(s string) (major, minor, patch int, ok bool) {
	parts := strings.Split(strings.TrimSpace(s), ".")
	if len(parts) == 0 || len(parts) > 3 {
		return 0, 0, 0, false
	}
	nums := [3]int{}
	for i, part := range parts {
		n, err := strconv.Atoi(part)
		if err != nil || n < 0 {
			return 0, 0, 0, false
		}
		nums[i] = n
	}
	return nums[0], nums[1], nums[2], true
}

func versionNewer(candidate, current string) bool {
	ca, cb, cc, cok := parseVersion(candidate)
	if !cok {
		return false
	}
	ua, ub, uc, uok := parseVersion(current)
	if !uok {
		return true
	}
	if ca != ua {
		return ca > ua
	}
	if cb != ub {
		return cb > ub
	}
	return cc > uc
}

func maybeUpdate(cfg *agentConfig) {
	if cfg == nil || !cfg.AutoUpdate || cfg.URL == "" {
		return
	}
	base := strings.TrimRight(cfg.URL, "/")
	body, err := httpGet(base + "/agent/v1/manifest")
	if err != nil {
		return
	}
	var man manifest
	if json.Unmarshal(body, &man) != nil {
		return
	}
	if man.Version == "" || !versionNewer(man.Version, version) {
		return
	}
	bin, ok := man.Binaries[archSKU()]
	if !ok || bin.SHA256 == "" {
		return
	}
	path := bin.Path
	if path == "" && bin.URL != "" {
		path = bin.URL
	}
	if path == "" {
		return
	}
	if !strings.HasPrefix(path, "http") {
		if !strings.HasPrefix(path, "/") {
			path = "/" + path
		}
		path = base + path
	}
	data, err := httpGet(path)
	if err != nil {
		return
	}
	sum := sha256.Sum256(data)
	got := hex.EncodeToString(sum[:])
	if !strings.EqualFold(got, bin.SHA256) {
		log.Printf("fuse-agent: update checksum mismatch")
		return
	}
	self, err := os.Executable()
	if err != nil {
		return
	}
	self, _ = filepath.Abs(self)
	neu := self + ".new"
	if err := os.WriteFile(neu, data, 0755); err != nil {
		return
	}
	log.Printf("fuse-agent: updated to %s, restarting", man.Version)
	if err := swapAndRelaunch(self, neu, cfg.Path); err != nil {
		log.Printf("fuse-agent: update restart failed: %v", err)
		os.Remove(neu)
		return
	}
	os.Exit(0)
}

func doUpdate(cfg *agentConfig) int {
	if cfg != nil {
		cfg.AutoUpdate = true
	}
	maybeUpdate(cfg)
	return 0
}
