//go:build windows

package main

import (
	"fmt"
	"os/exec"
	"syscall"
)

func swapAndRelaunch(self, neu, cfgPath string) error {
	old := self + ".old"
	var script string
	if serviceInstalled() {
		script = fmt.Sprintf(`ping 127.0.0.1 -n 3 >NUL & net stop %s >NUL 2>&1 & move /Y "%s" "%s" >NUL 2>&1 & move /Y "%s" "%s" >NUL & net start %s >NUL 2>&1`, serviceName, self, old, neu, self, serviceName)
	} else {
		script = fmt.Sprintf(`ping 127.0.0.1 -n 3 >NUL & move /Y "%s" "%s" >NUL 2>&1 & move /Y "%s" "%s" >NUL & start "" "%s" run --config "%s"`, self, old, neu, self, self, cfgPath)
	}
	cmd := exec.Command("cmd.exe", "/C", script)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: createNoWindow | windowsDetached,
	}
	return cmd.Start()
}

const windowsDetached = 0x00000008
