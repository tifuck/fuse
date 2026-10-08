//go:build !windows

package main

import (
	"os"
	"syscall"
)

// POSIX has no delayed move/start script: the rename onto the binary is
// atomic and the relaunch is a re-exec, so there is no helper lifetime to
// cover. The locked fd is accepted for signature parity; ownership still
// ends with this process.
func swapAndRelaunch(self, neu, cfgPath string, lock *os.File) error {
	_ = cfgPath
	_ = lock
	if err := os.Rename(neu, self); err != nil {
		return err
	}
	// Exec replaces this process with the new binary; returning an error here
	// would make maybeUpdate resume job intake on the old code with the on-disk
	// binary already swapped.
	return syscall.Exec(self, os.Args, os.Environ())
}

// doSwap is the Windows helper subcommand; not reachable on POSIX.
func doSwap(args []string) int {
	_ = args
	return 1
}
