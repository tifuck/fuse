//go:build !windows

package main

// hardenSecretFile is a no-op off Windows: os.WriteFile's 0600 mode already
// restricts the config file to the owning user on POSIX.
func hardenSecretFile(path string, previous string) error {
	return nil
}
