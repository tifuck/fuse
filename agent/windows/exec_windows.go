//go:build windows

package main

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"syscall"
	"time"

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

// swapWriter lets a pre-started PowerShell be handed to a job after Start:
// output goes to whichever job writer is set (nothing is printed before the
// command arrives on stdin).
type swapWriter struct {
	mu sync.Mutex
	w  io.Writer
}

func (s *swapWriter) set(w io.Writer) {
	s.mu.Lock()
	s.w = w
	s.mu.Unlock()
}

func (s *swapWriter) Write(p []byte) (int, error) {
	s.mu.Lock()
	w := s.w
	s.mu.Unlock()
	if w == nil {
		return len(p), nil
	}
	return w.Write(p)
}

// psProc is a started powershell waiting for its script on stdin, already in
// its own Job Object (so the whole tree can be terminated on timeout/cancel).
type psProc struct {
	cmd      *exec.Cmd
	stdin    io.WriteCloser
	out, err *swapWriter
	job      windows.Handle
}

func spawnPS(dir string) (*psProc, error) {
	ps := exec.Command(powershellPath(),
		"-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
		"-ExecutionPolicy", "Bypass",
		"-Command", "-")
	ps.Dir = dir
	// A background process (Start-Process, start /b) may keep the inherited
	// output pipes open: report the shell's result shortly after it exits.
	ps.WaitDelay = 250 * time.Millisecond
	ps.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: createNoWindow | createNewProcessGroup,
	}
	stdin, err := ps.StdinPipe()
	if err != nil {
		return nil, err
	}
	p := &psProc{cmd: ps, stdin: stdin, out: &swapWriter{}, err: &swapWriter{}}
	ps.Stdout, ps.Stderr = p.out, p.err
	// No KILL_ON_JOB_CLOSE: processes a command leaves running (a dev server
	// via Start-Process) keep running after it finishes, as on Linux. The job
	// only scopes TerminateJobObject on timeout and cancel.
	job, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		job = 0
	}
	if err := ps.Start(); err != nil {
		if job != 0 {
			windows.CloseHandle(job)
		}
		return nil, err
	}
	// Assigned before any script is written to stdin, so no descendant can
	// escape the job.
	if job != 0 {
		assigned := false
		if h, err := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE, false, uint32(ps.Process.Pid)); err == nil {
			assigned = windows.AssignProcessToJobObject(job, h) == nil
			windows.CloseHandle(h)
		}
		if !assigned {
			windows.CloseHandle(job)
			job = 0
		}
	}
	p.job = job
	return p, nil
}

func (p *psProc) alive() bool {
	h, err := windows.OpenProcess(windows.SYNCHRONIZE, false, uint32(p.cmd.Process.Pid))
	if err != nil {
		return false
	}
	defer windows.CloseHandle(h)
	ev, _ := windows.WaitForSingleObject(h, 0)
	return ev == uint32(windows.WAIT_TIMEOUT)
}

func (p *psProc) discard() {
	_ = p.cmd.Process.Kill()
	go func() {
		_ = p.cmd.Wait()
		if p.job != 0 {
			windows.CloseHandle(p.job)
		}
	}()
}

// One powershell is kept started: its cold start (loading .NET) dominated
// every command's latency. Taking it starts the next in the background.
var warm struct {
	sync.Mutex
	p       *psProc
	filling bool
}

func takeWarmPS() *psProc {
	warm.Lock()
	p := warm.p
	warm.p = nil
	fill := !warm.filling
	warm.filling = true
	warm.Unlock()
	if fill {
		go func() {
			q, err := spawnPS(userHome())
			warm.Lock()
			warm.filling = false
			if err == nil && warm.p == nil {
				warm.p, q = q, nil
			}
			warm.Unlock()
			if q != nil {
				q.discard()
			}
		}()
	}
	if p != nil && !p.alive() {
		p.discard()
		return nil
	}
	return p
}

func runExec(cwd, cmd string, timeoutMS int, cancel <-chan struct{}, setKill func(func()), onOut, onErr func([]byte)) (string, string, int, error, bool, bool) {
	if timeoutMS <= 0 {
		timeoutMS = 30000
	}
	if _, err := os.Stat(cwd); err != nil {
		return "", fmt.Sprintf("fuse-agent: chdir %s: %v\n", cwd, err), 127, nil, false, true
	}
	p := takeWarmPS()
	if p == nil {
		var err error
		if p, err = spawnPS(cwd); err != nil {
			return "", "", 1, err, false, true
		}
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
	p.out.set(outW)
	p.err.set(errW)
	ps := p.cmd
	// true when the whole tree is known dead (job terminated); a fallback
	// kill only reaches powershell, so its descendants may survive.
	kill := func() bool {
		if p.job != 0 && windows.TerminateJobObject(p.job, 1) == nil {
			return true
		}
		_ = ps.Process.Kill()
		return false
	}
	if setKill != nil {
		setKill(func() { kill() })
	}
	go func() {
		_, _ = io.WriteString(p.stdin, psBootstrap(cwd, cmd))
		_ = p.stdin.Close()
	}()

	done := make(chan error, 1)
	go func() { done <- ps.Wait() }()
	timer := time.NewTimer(time.Duration(timeoutMS) * time.Millisecond)
	defer timer.Stop()
	var waitErr error
	timedOut, killed, treeKilled := false, false, false
	select {
	case <-cancel:
		treeKilled, killed = kill(), true
		waitErr = <-done
	case <-timer.C:
		treeKilled, killed, timedOut = kill(), true, true
		waitErr = <-done
	case waitErr = <-done:
	}
	if p.job != 0 {
		windows.CloseHandle(p.job)
	}
	outW.flush()
	errW.flush()
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
	confirmed := !killed || treeKilled
	return stdout.String(), stderr.String(), code, nil, timedOut, confirmed
}
