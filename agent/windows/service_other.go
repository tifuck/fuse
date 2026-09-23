//go:build !windows

package main

func runAsService(string) int { return 1 }

func runningInService() bool { return false }
