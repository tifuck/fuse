//go:build !windows

package main

import (
	"os"
	"syscall"
)

// lockUpdateFile is the immediate-fail variant used by updaters.
func lockUpdateFile(f *os.File) error {
	return syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
}

// lockUpdateFileBlocking waits until the anchor can be locked (helper side
// of the handoff: the updater releases exactly to transfer ownership).
func lockUpdateFileBlocking(f *os.File) error {
	return syscall.Flock(int(f.Fd()), syscall.LOCK_EX)
}

func unlockUpdateFile(f *os.File) {
	_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
}
