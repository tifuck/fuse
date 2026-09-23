//go:build !windows

package main

func attachParentConsole() {}

func hideConsole() {}

func isElevated() bool { return false }

func serviceInstalled() bool { return false }

func serviceRunning() bool { return false }

func doInstall(cfg *agentConfig) int {
	logStderr("fuse-agent: install is Windows-only")
	return 1
}

func doUninstall() int {
	logStderr("fuse-agent: uninstall is Windows-only")
	return 1
}

func disableAutostart() {}

func logStderr(msg string) { println(msg) }
