//go:build !windows

package main

import "fmt"

func swapAndRelaunch(self, neu, cfgPath string) error {
	if err := osRename(neu, self); err != nil {
		return err
	}
	return fmt.Errorf("relaunch is Windows-only")
}

func osRename(a, b string) error { return nil }
