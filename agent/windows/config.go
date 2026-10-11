package main

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

type agentConfig struct {
	URL        string
	Token      string
	Code       string
	Path       string
	AutoUpdate bool
}

func loadConfig(path string) (*agentConfig, error) {
	cfg := &agentConfig{Path: path, AutoUpdate: true}
	f, err := os.Open(path)
	if err != nil {
		return cfg, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		switch strings.TrimSpace(k) {
		case "url":
			cfg.URL = strings.TrimSpace(v)
		case "token":
			cfg.Token = strings.TrimSpace(v)
		case "auto_update":
			n, _ := strconv.Atoi(strings.TrimSpace(v))
			cfg.AutoUpdate = n != 0
		}
	}
	return cfg, sc.Err()
}

func saveConfig(cfg *agentConfig) error {
	if cfg.Path == "" {
		return fmt.Errorf("missing config path")
	}
	if err := os.MkdirAll(filepath.Dir(cfg.Path), 0700); err != nil {
		return err
	}
	auto := 0
	if cfg.AutoUpdate {
		auto = 1
	}
	body := fmt.Sprintf("url=%s\ntoken=%s\nauto_update=%d\n", cfg.URL, cfg.Token, auto)
	tmp := cfg.Path + ".tmp"
	// A stale temp file may carry someone else's handle or ACL: never reuse it.
	if err := os.Remove(tmp); err != nil && !os.IsNotExist(err) {
		return err
	}
	// The token file is restricted from creation (see createSecretFile), so
	// no other local user can ever hold a handle to it.
	f, err := createSecretFile(tmp, cfg.Path)
	if err != nil {
		return err
	}
	if _, err := f.WriteString(body); err != nil {
		f.Close()
		os.Remove(tmp)
		return err
	}
	if err := f.Close(); err != nil {
		os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, cfg.Path)
}
