//go:build !windows

package main

import "os"

// createSecretFile creates path exclusively, 0600 from the first byte: on
// POSIX the mode bits already restrict the config to the owning user.
func createSecretFile(path string, previous string) (*os.File, error) {
	return os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
}
