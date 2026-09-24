package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"strings"
	"sync"
)

var version = "0.0.0"

func usage() {
	fmt.Fprintf(os.Stderr, "fuse-agent %s\n  run     [--url URL] [--code CODE] [--token TOK] [--config FILE]\n  install [--url URL] [--code CODE]\n  uninstall\n  update\n  service [--config FILE]\n  swap    (internal: commit helper)\n", version)
}

func parseArgs(args []string) (cmd string, cfg *agentConfig, headless bool) {
	cmd = "run"
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		cmd = args[0]
		args = args[1:]
	}
	fs := flag.NewFlagSet("fuse-agent", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	url := fs.String("url", "", "")
	code := fs.String("code", "", "")
	token := fs.String("token", "", "")
	config := fs.String("config", "", "")
	fg := fs.Bool("foreground", false, "")
	once := fs.Bool("once", false, "")
	hl := fs.Bool("headless", false, "")
	_ = fs.Parse(args)
	for _, a := range fs.Args() {
		if strings.HasPrefix(a, "fuse_") {
			*code = a
		}
	}
	cfg = &agentConfig{URL: *url, Token: *token, Code: *code, Path: *config, AutoUpdate: true}
	headless = *hl || *fg || *once
	return cmd, cfg, headless
}

func main() {
	log.SetFlags(0)
	log.SetPrefix("")
	// The swap helper is a subcommand of the same binary (Windows commit);
	// it must not go through the generic flag parsing below.
	if len(os.Args) > 1 && os.Args[1] == "swap" {
		os.Exit(doSwap(os.Args[2:]))
	}
	cmd, cfg, headless := parseArgs(os.Args[1:])
	if cmd == "-h" || cmd == "--help" || cmd == "help" {
		usage()
		os.Exit(0)
	}

	if cfg.Path == "" {
		if isElevated() {
			cfg.Path = defaultSystemConfigPath()
		} else {
			cfg.Path = defaultUserConfigPath()
		}
	}
	if loaded, err := loadConfig(cfg.Path); err == nil {
		if cfg.URL == "" {
			cfg.URL = loaded.URL
		}
		if cfg.Token == "" {
			cfg.Token = loaded.Token
		}
		cfg.AutoUpdate = loaded.AutoUpdate
		cfg.Path = loaded.Path
	}

	switch cmd {
	case "install":
		os.Exit(doInstall(cfg))
	case "uninstall":
		os.Exit(doUninstall())
	case "update":
		os.Exit(doUpdate(cfg))
	case "service":
		os.Exit(runAsService(cfg.Path))
	case "run":
		if cfg.URL == "" {
			die("missing --url or config url")
		}
		if cfg.Token == "" && cfg.Code == "" {
			die("missing --code/--token")
		}
		runInteractive(cfg, headless)
	default:
		usage()
		os.Exit(1)
	}
}

func die(msg string) {
	log.Printf("fuse-agent: %s", msg)
	os.Exit(1)
}

func runInteractive(cfg *agentConfig, headless bool) {
	if runningInService() {
		os.Exit(runAsService(cfg.Path))
	}
	hideConsole()
	stop := make(chan struct{})
	var once sync.Once
	quit := func(disable bool) {
		if disable {
			disableAutostart()
		}
		once.Do(func() { close(stop) })
	}
	onUpdate := func() {
		if serviceRunning() {
			// Jobs run in the service process; only it can reserve update
			// admission. Restarting the service from here would kill them.
			log.Printf("fuse-agent: update is owned by the running FuseAgent service; it will apply the update itself")
			return
		}
		s := activeSession.Load()
		if s == nil {
			log.Printf("fuse-agent: update skipped; connector session is not running")
			return
		}
		// Tray-triggered updates bypass the auto-update flag, not admission:
		// maybeUpdate holds the reservation across swap and exit.
		maybeUpdate(cfg, true, s.reserveUpdateAdmission)
	}
	startSession := true
	if serviceRunning() {
		startSession = false
	}
	if headless {
		runSession(cfg, stop)
		return
	}
	if startSession {
		go runSession(cfg, stop)
	} else {
		traySetStatus("online")
	}
	runTray(cfg, stop, quit, onUpdate)
}
