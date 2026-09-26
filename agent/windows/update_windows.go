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
		_ = exec.Command("net", "stop", serviceName).Run()
	}
	time.Sleep(2 * time.Second) // let the stopped updater's handles drop
	old := target + ".old"
	if err := os.Rename(target, old); err != nil {
		log.Printf("fuse-agent: swap helper: %v", err)
		return 1
	}
	if err := os.Rename(staged, target); err != nil {
		_ = os.Rename(old, target) // restore
		log.Printf("fuse-agent: swap helper: %v", err)
		return 1
	}
	if asService {
		_ = exec.Command("net", "start", serviceName).Run()
	} else if config != "" {
		c := exec.Command(target, "run", "--config", config)
		c.SysProcAttr = &syscall.SysProcAttr{
			HideWindow:    true,
			CreationFlags: createNoWindow | windowsDetached | createNewProcessGroup,
		}
		_ = c.Start()
	}
	// Lock is released when the helper exits; until then no contender can
	// commit. The parent has already exited by the time the moves ran.
	return 0
}
