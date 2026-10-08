//go:build windows

package main

import (
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

const (
	runValueName = "FuseAgent"
	serviceName  = "FuseAgent"
	serviceDisp  = "Fuse Connector"
)

func attachParentConsole() {
	mod := windows.NewLazySystemDLL("kernel32.dll")
	attach := mod.NewProc("AttachConsole")
	r, _, _ := attach.Call(^uintptr(0))
	if r == 0 {
		return
	}
	os.Stdout = os.NewFile(uintptr(syscall.Stdout), "stdout")
	os.Stderr = os.NewFile(uintptr(syscall.Stderr), "stderr")
	log.SetOutput(os.Stderr)
}

func isElevated() bool {
	var token windows.Token
	err := windows.OpenProcessToken(windows.CurrentProcess(), windows.TOKEN_QUERY, &token)
	if err != nil {
		return false
	}
	defer token.Close()
	return token.IsElevated()
}

func copyFile(src, dst string) error {
	if err := os.MkdirAll(filepath.Dir(dst), 0755); err != nil {
		return err
	}
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	tmp := dst + ".new"
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0755)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		os.Remove(tmp)
		return err
	}
	if err := out.Sync(); err != nil {
		out.Close()
		os.Remove(tmp)
		return err
	}
	if err := out.Close(); err != nil {
		os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, dst); err != nil {
		// Keep the previous binary until the new one is in place: removing it
		// first left nothing behind when the second rename also failed.
		bak := dst + ".old"
		_ = os.Remove(bak)
		if err2 := os.Rename(dst, bak); err2 != nil {
			os.Remove(tmp)
			return err
		}
		if err2 := os.Rename(tmp, dst); err2 != nil {
			_ = os.Rename(bak, dst) // restore
			os.Remove(tmp)
			return err2
		}
		_ = os.Remove(bak)
	}
	return nil
}

func selfPath() (string, error) {
	p, err := os.Executable()
	if err != nil {
		return "", err
	}
	return filepath.EvalSymlinks(p)
}

func writeRunKey(cmdline string) error {
	k, _, err := registry.CreateKey(registry.CURRENT_USER,
		`Software\Microsoft\Windows\CurrentVersion\Run`, registry.SET_VALUE)
	if err != nil {
		return err
	}
	defer k.Close()
	return k.SetStringValue(runValueName, cmdline)
}

func deleteRunKey() {
	k, err := registry.OpenKey(registry.CURRENT_USER,
		`Software\Microsoft\Windows\CurrentVersion\Run`, registry.SET_VALUE)
	if err != nil {
		return
	}
	defer k.Close()
	_ = k.DeleteValue(runValueName)
}

func runCommandLine(bin, cfg string) string {
	return fmt.Sprintf(`"%s" run --config "%s"`, bin, cfg)
}

func startDetached(bin string, args ...string) error {
	cmd := exec.Command(bin, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: 0x00000008 | createNoWindow, // DETACHED_PROCESS
	}
	return cmd.Start()
}

func serviceInstalled() bool {
	m, err := mgr.Connect()
	if err != nil {
		return false
	}
	defer m.Disconnect()
	s, err := m.OpenService(serviceName)
	if err != nil {
		return false
	}
	s.Close()
	return true
}

func serviceRunning() bool {
	m, err := mgr.Connect()
	if err != nil {
		return false
	}
	defer m.Disconnect()
	s, err := m.OpenService(serviceName)
	if err != nil {
		return false
	}
	defer s.Close()
	st, err := s.Query()
	if err != nil {
		return false
	}
	return st.State == svc.Running
}

func installWindowsService(bin, cfg string) error {
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()
	c := mgr.Config{
		DisplayName: serviceDisp,
		StartType:   mgr.StartAutomatic,
		Description: "Outbound Fuse connector. Lets your Fuse account run PowerShell on this machine.",
	}
	s, err := m.OpenService(serviceName)
	if err == nil {
		_ = s.UpdateConfig(c)
		s.Close()
	} else {
		s, err = m.CreateService(serviceName, bin, c, "service", "--config", cfg)
		if err != nil {
			return err
		}
		s.Close()
	}
	s, err = m.OpenService(serviceName)
	if err != nil {
		return err
	}
	defer s.Close()
	return s.Start()
}

func removeWindowsService() {
	m, err := mgr.Connect()
	if err != nil {
		return
	}
	defer m.Disconnect()
	s, err := m.OpenService(serviceName)
	if err != nil {
		return
	}
	_, _ = s.Control(svc.Stop)
	_ = s.Delete()
	s.Close()
}

func doInstall(cfg *agentConfig) int {
	attachParentConsole()
	if cfg.URL == "" {
		log.Printf("fuse-agent: install requires --url")
		return 1
	}
	elev := isElevated()
	if elev {
		fmt.Fprintln(os.Stderr, "fuse-agent: installing elevated; the paired Fuse account can run PowerShell as this service account, including at the lock screen. Prefer an unelevated install if the workload allows it.")
	}
	self, err := selfPath()
	if err != nil {
		log.Printf("fuse-agent: %v", err)
		return 1
	}
	var bin, conf string
	if elev {
		bin = defaultSystemBinPath()
		conf = defaultSystemConfigPath()
	} else {
		bin = defaultUserBinPath()
		conf = defaultUserConfigPath()
	}
	if err := copyFile(self, bin); err != nil {
		log.Printf("fuse-agent: could not install binary to %s: %v", bin, err)
		return 1
	}
	cfg.Path = conf
	cfg.AutoUpdate = true
	if cfg.Code != "" || cfg.Token == "" {
		if err := pairOnce(cfg); err != nil {
			log.Printf("fuse-agent: %v", err)
			return 1
		}
	}
	if err := saveConfig(cfg); err != nil {
		log.Printf("fuse-agent: could not write %s: %v", conf, err)
		return 1
	}
	cmdline := runCommandLine(bin, conf)
	if err := writeRunKey(cmdline); err != nil {
		log.Printf("fuse-agent: could not write logon autostart: %v", err)
	}
	if elev {
		if err := installWindowsService(bin, conf); err != nil {
			log.Printf("fuse-agent: could not install boot service: %v", err)
			return 1
		}
		if err := startDetached(bin, "run", "--config", conf); err != nil {
			log.Printf("fuse-agent: started service; tray launch failed: %v", err)
		}
		fmt.Fprintln(os.Stderr, "fuse-agent: installed and started (service + tray)")
		return 0
	}
	if err := startDetached(bin, "run", "--config", conf); err != nil {
		log.Printf("fuse-agent: installed but could not start: %v", err)
		return 1
	}
	fmt.Fprintln(os.Stderr, "fuse-agent: installed and started (user)")
	return 0
}

func doUninstall() int {
	attachParentConsole()
	deleteRunKey()
	if isElevated() {
		removeWindowsService()
	}
	fmt.Fprintln(os.Stderr, "fuse-agent: uninstalled autostart")
	return 0
}

func disableAutostart() {
	deleteRunKey()
}

func hideConsole() {
	mod := windows.NewLazySystemDLL("kernel32.dll")
	get := mod.NewProc("GetConsoleWindow")
	hwnd, _, _ := get.Call()
	if hwnd != 0 {
		user32 := windows.NewLazySystemDLL("user32.dll")
		show := user32.NewProc("ShowWindow")
		show.Call(hwnd, 0) // SW_HIDE
	}
	free := mod.NewProc("FreeConsole")
	free.Call()
}
