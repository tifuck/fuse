//go:build windows

package main

import (
	"bytes"
	"context"
	"errors"
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

func runExec(cwd, cmd string, timeoutMS int, cancel <-chan struct{}, setKill func(func()), onOut, onErr func([]byte)) (string, string, int, error, bool, bool) {
	if timeoutMS <= 0 {
		timeoutMS = 30000
	}
	if _, err := os.Stat(cwd); err != nil {
		return "", fmt.Sprintf("fuse-agent: chdir %s: %v\n", cwd, err), 127, nil, false, true
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
		return "", "", 1, err, false, true
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
		return "", "", 1, err, false, true
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
	killed := false
	select {
	case <-cancel:
		kill()
		killed = true
		waitErr = <-done
		cancelled = true
	case <-ctx.Done():
		kill()
		killed = true
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
		} else if errors.Is(waitErr, exec.ErrWaitDelay) && ps.ProcessState != nil {
			// The shell exited but a child (Start-Process, start /b) still
			// held the output pipe: report the shell's own exit status, not
			// a failure, and say the output may be incomplete.
			code = ps.ProcessState.ExitCode()
			stderr.WriteString("\n[fuse-agent: a background process kept the output open; output may be incomplete]\n")
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
	// Termination is confirmed when no kill ran (the shell exited on its own),
	// or when the job object covered the tree: closing its handle above reaped
	// every descendant. A kill that fell back to Process.Kill only reached
	// powershell, so its descendants may still be running.
	confirmed := !killed || job != 0
	return stdout.String(), stderr.String(), code, nil, timedOut, confirmed
}
