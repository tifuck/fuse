//go:build windows

package main

import (
	"os"

	"golang.org/x/sys/windows"
)

// Byte-range locks on Windows are PROCESS-owned: an inherited handle does
// not give the child access to the locked region, and the locks are released
// when the owning process terminates. Therefore the commit path never relies
// on a lock surviving death: the swap helper (self swap) acquires its own
// lock, with an explicit readiness handshake, before the updater exits.
// lockUpdateFile is the immediate-fail variant used by updaters.
func lockUpdateFile(f *os.File) error {
	ol := &windows.Overlapped{}
	return windows.LockFileEx(windows.Handle(f.Fd()),
		windows.LOCKFILE_EXCLUSIVE_LOCK|windows.LOCKFILE_FAIL_IMMEDIATELY,
		0, 1, 0, ol)
}

// lockUpdateFileBlocking waits until the anchor can be locked. It is the
// helper side of the handoff: the updater releases exactly to transfer
// ownership, and the helper blocks until it owns the region itself.
func lockUpdateFileBlocking(f *os.File) error {
	ol := &windows.Overlapped{}
	return windows.LockFileEx(windows.Handle(f.Fd()),
		windows.LOCKFILE_EXCLUSIVE_LOCK, 0, 1, 0, ol)
}

func unlockUpdateFile(f *os.File) {
	ol := &windows.Overlapped{}
	_ = windows.UnlockFileEx(windows.Handle(f.Fd()), 0, 1, 0, ol)
}
