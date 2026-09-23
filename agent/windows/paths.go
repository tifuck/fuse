package main

import (
	"os"
	"path/filepath"
)

func defaultUserConfigPath() string {
	base := os.Getenv("APPDATA")
	if base == "" {
		base = filepath.Join(userHome(), "AppData", "Roaming")
	}
	return filepath.Join(base, "FuseAgent", "agent.conf")
}

func defaultUserBinPath() string {
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		base = filepath.Join(userHome(), "AppData", "Local")
	}
	return filepath.Join(base, "FuseAgent", "fuse-agent.exe")
}

func defaultSystemConfigPath() string {
	base := os.Getenv("PROGRAMDATA")
	if base == "" {
		base = `C:\ProgramData`
	}
	return filepath.Join(base, "FuseAgent", "agent.conf")
}

func defaultSystemBinPath() string {
	base := os.Getenv("PROGRAMFILES")
	if base == "" {
		base = `C:\Program Files`
	}
	return filepath.Join(base, "FuseAgent", "fuse-agent.exe")
}
