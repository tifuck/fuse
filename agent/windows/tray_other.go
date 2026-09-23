//go:build !windows

package main

func traySetStatus(string) {}

func runTray(cfg *agentConfig, stop chan struct{}, onQuit func(bool), onUpdate func()) {
	<-stop
}
