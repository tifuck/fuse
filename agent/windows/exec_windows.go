//go:build windows

package main

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

const (
	createNoWindow        = 0x08000000
	createNewProcessGroup = 0x00000200
)

func powershellPath() string {
	if p, err := exec.LookPath("powershell.exe"); err == nil {
		return p
	}
	sys := os.Getenv("SystemRoot")
	if sys == "" {
		sys = `C:\Windows`
	}
	return filepath.Join(sys, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
}

type streamWriter struct {
	on  func([]byte)
	buf *bytes.Buffer
	n   int
	cap int
}

func (w *streamWriter) Write(p []byte) (int, error) {
	if w.on != nil && len(p) > 0 {
		w.on(p)
	}
	if w.buf != nil && w.n < w.cap {
		left := w.cap - w.n
		if len(p) > left {
			w.buf.Write(p[:left])
		} else {
			w.buf.Write(p)
		}
	}
	w.n += len(p)
	return len(p), nil
}

func runExec(cwd, cmd string, timeoutMS int, cancel <-chan struct{}, setKill func(func()), onOut, onErr func([]byte)) (string, string, int, error, bool) {
	if timeoutMS <= 0 {
		timeoutMS = 30000
	}
	if _, err := os.Stat(cwd); err != nil {
		return "", fmt.Sprintf("fuse-agent: chdir %s: %v\n", cwd, err), 127, nil, false
	}
	ctx, cancelFn := context.WithTimeout(context.Background(), time.Duration(timeoutMS)*time.Millisecond)
	defer cancelFn()

	ps := exec.CommandContext(ctx, powershellPath(),
		"-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
		"-ExecutionPolicy", "Bypass",
		"-Command", "-")
	ps.Dir = cwd
	ps.WaitDelay = 5 * time.Second // descendants must not hold inherited output pipes forever
	ps.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: createNoWindow | createNewProcessGroup,
	}
	preamble := "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; $OutputEncoding = [Console]::OutputEncoding; $ErrorActionPreference = 'Continue'\n"
	stdin, err := ps.StdinPipe()
	if err != nil {
		return "", "", 1, err, false
	}
	var stdout, stderr bytes.Buffer
	outW := &streamWriter{on: onOut, cap: outCap}
	errW := &streamWriter{on: onErr, cap: outCap}
	if onOut == nil {
		outW.buf = &stdout
	}
	if onErr == nil {
		errW.buf = &stderr
	}
	ps.Stdout = outW
	ps.Stderr = errW

	job, err := windows.CreateJobObject(nil, nil)
	if err == nil {
		var info windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION
		info.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
		_, setupErr := windows.SetInformationJobObject(job, windows.JobObjectExtendedLimitInformation,
			uintptr(unsafe.Pointer(&info)), uint32(unsafe.Sizeof(info)))
		if setupErr != nil {
			windows.CloseHandle(job)
			job = 0
		}
	}

	if err := ps.Start(); err != nil {
		if job != 0 {
			windows.CloseHandle(job)
		}
		return "", "", 1, err, false
	}
	if job != 0 && ps.Process != nil {
		assigned := false
		if h, err := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE|windows.PROCESS_SUSPEND_RESUME, false, uint32(ps.Process.Pid)); err == nil {
			assigned = windows.AssignProcessToJobObject(job, h) == nil
			windows.CloseHandle(h)
		}
		if !assigned {
			windows.CloseHandle(job)
			job = 0
		}
	}
	kill := func() {
		if job != 0 {
			if windows.TerminateJobObject(job, 1) == nil {
				return
			}
		}
		if ps.Process != nil {
			_ = ps.Process.Kill()
		}
	}
	if setKill != nil {
		setKill(kill)
	}
	go func() {
		_, _ = stdin.Write([]byte(preamble + cmd + "\n"))
		_ = stdin.Close()
	}()

	done := make(chan error, 1)
	go func() { done <- ps.Wait() }()
	var waitErr error
	timedOut := false
	cancelled := false
	select {
	case <-cancel:
		kill()
		waitErr = <-done
		cancelled = true
	case <-ctx.Done():
		kill()
		waitErr = <-done
		timedOut = ctx.Err() == context.DeadlineExceeded
	case waitErr = <-done:
	}
	if !cancelled && ctx.Err() == context.DeadlineExceeded {
		timedOut = true
	}
	if job != 0 {
		windows.CloseHandle(job)
	}
	code := 0
	if waitErr != nil {
		if ee, ok := waitErr.(*exec.ExitError); ok {
			code = ee.ExitCode()
		} else if ctx.Err() != nil {
			code = 1
		} else {
			code = 1
		}
	}
	// Preserve overflow information until sendResFull sets the protocol flag.
	if outW.n > outCap {
		stdout.WriteString("\n[fuse-agent: output truncated]\n")
	}
	if errW.n > outCap {
		stderr.WriteString("\n[fuse-agent: output truncated]\n")
	}
	return stdout.String(), stderr.String(), code, nil, timedOut
}
