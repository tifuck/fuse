//go:build !windows

package main

import "fmt"

func runExec(cwd, cmd string, timeoutMS int, cancel <-chan struct{}, setKill func(func()), onOut, onErr func([]byte)) (string, string, int, error, bool) {
	return "", "", 1, fmt.Errorf("windows only"), false
}
