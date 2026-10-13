//go:build windows

package main

import (
	"bufio"
	"flag"
	"fmt"
	"log"
	"os"
	"os/exec"
	"syscall"
	"time"
)

const windowsDetached = 0x00000008

// Handoff timeouts: how long the updater waits for the helper to report lock
// ownership before aborting its own commit.
const swapReadyTimeout = 2 * time.Minute

// swapAndRelaunch commits the verified update through a helper process
// (self swap) that OWNS the update lock itself:
//
//	updater: spawn helper -> release lock -> await "ready" -> 'g' -> exit
//	helper:  blocking-acquire -> "ready" -> 'g' -> net stop -> moves ->
//	         net start / relaunch -> release -> exit
//
// Ownership transfers kernel-verified: the helper holds the byte-range lock
// from before the updater exits until after the final move/start, so two
// commits can never overlap. A contender that wins the transfer window
// simply becomes the owner; the helper still blocks until it finishes, and
// the updater aborts its own commit instead of racing. Job admission stays
// held in the updater until it exits (after "ready"), so no job can start
// under a soon-to-die process.
func swapAndRelaunch(self, neu, cfgPath string, lock *os.File) error {
	args := []string{"swap", "--target", self, "--staged", neu, "--config", cfgPath}
	if serviceInstalled() {
		args = append(args, "--service")
	}
	cmd := exec.Command(self, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: createNoWindow | windowsDetached | createNewProcessGroup,
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	// Hand ownership over: the helper is blocked acquiring; releasing here
	// lets it take the lock before this process exits.
	unlockUpdateFile(lock)
	lock.Close()

	ready := make(chan error, 1)
	go func() {
		line, rerr := bufio.NewReader(stdout).ReadString('\n')
		switch {
		case rerr != nil:
			ready <- rerr
		case line != "ready\n":
			ready <- fmt.Errorf("unexpected helper line %q", line)
		default:
			ready <- nil
		}
	}()
	var herr error
	select {
	case herr = <-ready:
	case <-time.After(swapReadyTimeout):
		herr = fmt.Errorf("swap helper did not become ready")
	}
	if herr != nil {
		// Stand the helper down: it will read 'a' as soon as it owns the
		// lock (possibly after a competing updater finishes) and abort on
		// its own. Do NOT wait here - the helper may be parked on the lock
		// for as long as the contender holds it.
		_, _ = stdin.Write([]byte("a"))
		stdin.Close()
		return herr
	}
	if _, err := stdin.Write([]byte("g")); err != nil {
		// Helper is past lock ownership; kill it deterministically.
		if cmd.Process != nil {
			cmd.Process.Kill()
		}
		_ = cmd.Wait()
		return err
	}
	stdin.Close()
	return nil
}

// doSwap runs as the helper process: `fuse-agent swap --target ... --staged ...
// [--service] [--config ...]`. It first OWNS the update lock (blocking
// acquire - the updater releases exactly to transfer it), then answers the
// readiness handshake, and only proceeds on 'g'.
func doSwap(args []string) int {
	fs := flag.NewFlagSet("swap", flag.ContinueOnError)
	target := fs.String("target", "", "")
	staged := fs.String("staged", "", "")
	config := fs.String("config", "", "")
	asService := fs.Bool("service", false, "")
	if fs.Parse(args) != nil || *target == "" || *staged == "" {
		return 1
	}
	return runSwapHelper(*target, *staged, *config, *asService)
}

func runSwapHelper(target, staged, config string, asService bool) int {
	// A detached helper has no stderr: keep its log next to the binary, or a
	// failed swap leaves no trace at all.
	if lf, err := os.OpenFile(target+".update.log", os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600); err == nil {
		defer lf.Close()
		log.SetOutput(lf)
	}
	f, err := os.OpenFile(updateLockPath(target), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		log.Printf("fuse-agent: swap helper: %v", err)
		return 1
	}
	defer f.Close()
	// Own the lock BEFORE approving the parent's exit.
	if err := lockUpdateFileBlocking(f); err != nil {
		log.Printf("fuse-agent: swap helper: %v", err)
		return 1
	}
	fmt.Println("ready")
	b := make([]byte, 1)
	n, _ := os.Stdin.Read(b)
	if n != 1 || b[0] != 'g' {
		log.Printf("fuse-agent: swap aborted before commit")
		return 1
	}
	if asService {
		if out, err := exec.Command("net", "stop", serviceName).CombinedOutput(); err != nil {
			log.Printf("fuse-agent: swap helper: net stop: %v: %s", err, out)
		}
	}
	time.Sleep(2 * time.Second) // let the stopped updater's handles drop
	// From here on every exit restarts the connector: the updater already
	// exited, so returning without a restart left the machine offline until
	// reboot.
	rc := 0
	if err := swapBinaries(target, staged); err != nil {
		log.Printf("fuse-agent: swap helper: %v; restarting the current binary", err)
		rc = 1
	}
	if asService {
		if out, err := exec.Command("net", "start", serviceName).CombinedOutput(); err != nil {
			log.Printf("fuse-agent: swap helper: net start: %v: %s", err, out)
			rc = 1
		}
	} else if config != "" {
		c := exec.Command(target, "run", "--config", config)
		c.SysProcAttr = &syscall.SysProcAttr{
			HideWindow:    true,
			CreationFlags: createNoWindow | windowsDetached | createNewProcessGroup,
		}
		if err := c.Start(); err != nil {
			log.Printf("fuse-agent: swap helper: relaunch: %v", err)
			rc = 1
		}
	}
	// Lock is released when the helper exits; until then no contender can
	// commit. The parent has already exited by the time the moves ran.
	return rc
}

// swapBinaries moves target aside and staged into place, restoring target on
// failure. The backup name is unique when a previous one cannot be removed:
// the tray from an earlier update may still be running from target.old, and
// a running image cannot be replaced.
func swapBinaries(target, staged string) error {
	old := target + ".old"
	if err := os.Remove(old); err != nil && !os.IsNotExist(err) {
		old = fmt.Sprintf("%s.old-%d", target, time.Now().UnixNano())
	}
	if err := os.Rename(target, old); err != nil {
		return err
	}
	if err := os.Rename(staged, target); err != nil {
		if rerr := os.Rename(old, target); rerr != nil {
			return fmt.Errorf("%v; restoring %s failed: %v", err, target, rerr)
		}
		return err
	}
	return nil
}
