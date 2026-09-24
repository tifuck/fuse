//go:build !windows

package main

import (
	"fmt"
	"os"
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
	return fmt.Errorf("relaunch is Windows-only")
}

// doSwap is the Windows helper subcommand; not reachable on POSIX.
func doSwap(args []string) int {
	_ = args
	return 1
}
