//go:build !windows

package main

// copyFileSecurity is a no-op off Windows: the mode bits copied by the
// atomic write already carry the original file's access.
func copyFileSecurity(src, dst string) error { return nil }
