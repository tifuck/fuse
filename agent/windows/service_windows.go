//go:build windows

package main

import (
	"log"

	"golang.org/x/sys/windows/svc"
)

type fuseSvc struct {
	cfgPath string
}

func (m *fuseSvc) Execute(args []string, r <-chan svc.ChangeRequest, changes chan<- svc.Status) (ssec bool, errno uint32) {
	changes <- svc.Status{State: svc.StartPending}
	cfg, err := loadConfig(m.cfgPath)
	if err != nil && cfg.URL == "" {
		log.Printf("fuse-agent: %v", err)
		return false, 1
	}
	stop := make(chan struct{})
	go runSession(cfg, stop)
	changes <- svc.Status{State: svc.Running, Accepts: svc.AcceptStop | svc.AcceptShutdown}
	for c := range r {
		switch c.Cmd {
		case svc.Interrogate:
			changes <- c.CurrentStatus
		case svc.Stop, svc.Shutdown:
			close(stop)
			changes <- svc.Status{State: svc.StopPending}
			return false, 0
		}
	}
	return false, 0
}

func runAsService(cfgPath string) int {
	if err := svc.Run(serviceName, &fuseSvc{cfgPath: cfgPath}); err != nil {
		log.Printf("fuse-agent: service: %v", err)
		return 1
	}
	return 0
}

func runningInService() bool {
	ok, err := svc.IsWindowsService()
	return err == nil && ok
}
