package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sync"
	"testing"
	"time"
)

const (
	lockHandoffEnv    = "FUSE_TEST_LOCK_HANDOFF"
	lockHandoffAnchor = "FUSE_TEST_LOCK_ANCHOR"
	handoffTimeout    = 10 * time.Second
)

// Coalesces this process's own update callers before the cross-process lock.
func TestUpdateSingleFlight(t *testing.T) {
	if !tryBeginUpdate() {
		t.Fatal("first begin refused")
	}
	if tryBeginUpdate() {
		t.Fatal("second concurrent in-process update admitted")
	}
	endUpdate()
	if !tryBeginUpdate() {
		t.Fatal("begin after end refused")
	}
	endUpdate()
}

// Cross-process ownership: the anchor is kernel-locked (flock/LockFileEx on
// the open handle, never unlinked), a second updater is refused while held,
// ownership transfers on release, and release is idempotent.
func TestUpdateLockCrossProcess(t *testing.T) {
	self := filepath.Join(t.TempDir(), "fuse-agent.exe")
	release, _, ok := acquireUpdateLock(self)
	if !ok {
		t.Fatal("acquire on fresh lock failed")
	}
	if _, _, ok := acquireUpdateLock(self); ok {
		t.Fatal("second updater admitted while lock held")
	}
	release()
	release2, _, ok := acquireUpdateLock(self)
	if !ok {
		t.Fatal("reacquire after release failed")
	}
	if _, err := os.Stat(updateLockPath(self)); err != nil {
		t.Fatalf("lock anchor must survive release: %v", err)
	}
	release2()
	release2() // idempotent: must not unlock a later owner
	release3, _, ok := acquireUpdateLock(self)
	if !ok {
		t.Fatal("reacquire after idempotent release failed")
	}
	release3()
}

// Independent handles: closing one must not release the owner's exclusive
// lock (ownership lives on the locked handle, not the file).
func TestUpdateLockSurvivesOtherHandleClose(t *testing.T) {
	self := filepath.Join(t.TempDir(), "fuse-agent.exe")
	hold, _, ok := acquireUpdateLock(self)
	if !ok {
		t.Fatal("first owner refused")
	}
	defer hold()
	f, err := os.OpenFile(updateLockPath(self), os.O_RDWR, 0600)
	if err != nil {
		t.Fatal(err)
	}
	f.Close()
	if _, _, ok := acquireUpdateLock(self); ok {
		t.Fatal("lock lost when an unrelated handle closed")
	}
}

// Staging must be exclusive per write, never a fixed or shared name a second
// updater (or a crashed leftover) could truncate under the verifier.
func TestStageUpdateBinaryExclusive(t *testing.T) {
	dir := t.TempDir()
	self := filepath.Join(dir, "fuse-agent.exe")
	p1, err := stageUpdateBinary(self, []byte("bin1"))
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(p1)
	p2, err := stageUpdateBinary(self, []byte("bin2"))
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(p2)
	if p1 == p2 {
		t.Fatal("staging path shared between stages")
	}
	if filepath.Base(p1) == "fuse-agent.exe.new" {
		t.Fatal("staging path collides with historical fixed name")
	}
	// Unix permission bits only exist on POSIX; Windows reports rw-rw-rw-.
	if st, err := os.Stat(p1); err != nil || (runtime.GOOS != "windows" && st.Mode().Perm() != 0755) {
		t.Fatalf("staged mode %v err %v", st.Mode().Perm(), err)
	}
}

// Admission is a held reservation, not a one-off check: while held, job
// intake is refused (never queued), so a swap+exit can never kill work that
// arrived after admission was granted.
func TestUpdateAdmissionPausesIntake(t *testing.T) {
	s := &session{jobs: newJobHub()}
	hold, ok := s.reserveUpdateAdmission()
	if !ok {
		t.Fatal("idle session refused admission")
	}
	s.jobs.handle(s, map[string]any{"id": "j1", "op": "exec", "cmd": "x"})
	s.jobs.mu.Lock()
	live, paused := len(s.jobs.live), s.jobs.paused
	s.jobs.mu.Unlock()
	if live != 0 {
		t.Fatalf("intake registered during admission: live=%d", live)
	}
	if !paused {
		t.Fatal("admission flag lost")
	}
	if len(s.results) == 0 {
		t.Fatal("no busy refusal sent for refused job")
	}
	hold()
	s.jobs.mu.Lock()
	paused = s.jobs.paused
	s.jobs.mu.Unlock()
	if paused {
		t.Fatal("admission not released")
	}
}

func TestUpdateAdmissionRefusesBusy(t *testing.T) {
	s := &session{jobs: newJobHub()}
	s.jobs.live["live"] = &liveJob{id: "live", cancel: make(chan struct{})}
	if _, ok := s.reserveUpdateAdmission(); ok {
		t.Fatal("admission granted with a live job; exit would kill it")
	}
	s2 := &session{jobs: newJobHub()}
	s2.sendRes("old", true, 0, "undelivered", "", "")
	if _, ok := s2.reserveUpdateAdmission(); ok {
		t.Fatal("admission granted with undelivered results")
	}
}

// wakeFn publication: concurrent notifyWake from many goroutines (tray
// window thread, network callback) must read a fully constructed callback.
func TestWakePublicationRaceFree(t *testing.T) {
	var mu sync.Mutex
	last := 0
	setWakeFn(func(reason int) {
		mu.Lock()
		last = reason
		mu.Unlock()
	})
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			notifyWake(wakeNetwork)
		}()
	}
	wg.Wait()
	mu.Lock()
	defer mu.Unlock()
	if last != wakeNetwork {
		t.Fatalf("wake lost: %d", last)
	}
	setWakeFn(nil)
	notifyWake(wakeResume) // nil callback must not panic
}

// Handoff regression: the helper owns the lock itself, gated by a readiness
// handshake. 'g' approves the commit and ownership ends with the helper
// (re-acquirable afterwards); 'a' aborts before touching anything.
// Exercises the real blocking-acquire + handshake on the running platform.
func TestLockHandoffReadyGate(t *testing.T) {
	if os.Getenv(lockHandoffEnv) == "1" {
		anchor := os.Getenv(lockHandoffAnchor)
		f, err := os.OpenFile(anchor, os.O_CREATE|os.O_RDWR, 0600)
		if err != nil {
			os.Exit(2)
		}
		defer f.Close()
		// Blocks until the parent releases - the ownership transfer itself.
		if err := lockUpdateFileBlocking(f); err != nil {
			os.Exit(3)
		}
		fmt.Println("ready")
		b := make([]byte, 1)
		n, _ := os.Stdin.Read(b)
		f.Close()
		if n == 1 && b[0] == 'g' {
			os.Exit(0)
		}
		os.Exit(4)
	}

	t.Run("approve", func(t *testing.T) { runHandoff(t, 'g', 0) })
	t.Run("abort", func(t *testing.T) { runHandoff(t, 'a', 4) })
}

func runHandoff(t *testing.T, reply byte, wantExit int) {
	t.Helper()
	self := filepath.Join(t.TempDir(), "fuse-agent.exe")
	parentRelease, _, ok := acquireUpdateLock(self)
	if !ok {
		t.Fatal("acquire failed")
	}
	cmd := exec.Command(os.Args[0], "-test.run=TestLockHandoffReadyGate")
	cmd.Env = append(os.Environ(),
		lockHandoffEnv+"=1",
		lockHandoffAnchor+"="+updateLockPath(self),
	)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	// While the parent still owns the lock, a contender is refused.
	if _, _, ok := acquireUpdateLock(self); ok {
		parentRelease()
		t.Fatal("contender acquired before handoff")
	}
	parentRelease() // the ownership transfer: helper's blocking acquire lands

	ready := make(chan error, 1)
	go func() {
		buf := make([]byte, 16)
		n, err := stdout.Read(buf)
		if err != nil {
			ready <- err
			return
		}
		if string(buf[:n]) != "ready\n" {
			ready <- fmt.Errorf("unexpected helper output %q", buf[:n])
			return
		}
		ready <- nil
	}()
	select {
	case err := <-ready:
		if err != nil {
			t.Fatalf("helper not ready: %v", err)
		}
	case <-time.After(handoffTimeout):
		t.Fatal("helper never reported ready")
	}
	// Helper owns the lock now: contender still refused.
	if _, _, ok := acquireUpdateLock(self); ok {
		t.Fatal("contender acquired during helper ownership")
	}
	if _, err := stdin.Write([]byte{reply}); err != nil {
		t.Fatal(err)
	}
	stdin.Close()
	err = cmd.Wait()
	if wantExit == 0 {
		if err != nil {
			t.Fatalf("approved helper failed: %v", err)
		}
	} else {
		ee, ok := err.(*exec.ExitError)
		if !ok || ee.ExitCode() != wantExit {
			t.Fatalf("abort exit = %v, want %d", err, wantExit)
		}
	}
	// Ownership ended with the helper: re-acquirable. Release it again so
	// the handle closes (Windows cannot delete the temp dir around an open file).
	release, _, ok := acquireUpdateLock(self)
	if !ok {
		t.Fatal("lock not re-acquirable after helper completion")
	}
	release()
}
