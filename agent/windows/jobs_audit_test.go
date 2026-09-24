package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf8"
)

func TestResultCarriesExplicitFalseTruncation(t *testing.T) {
	data, err := json.Marshal(jobRes{ID: "job"})
	if err != nil || !strings.Contains(string(data), `"truncated":false`) {
		t.Fatalf("no positive completeness evidence: %s %v", data, err)
	}
}

func TestResultsSurviveOfflineAndFailedFlush(t *testing.T) {
	s := &session{}
	s.sendRes("first", true, 0, "retained", "", "")
	s.sendRes("second", true, 0, "next", "", "")
	if len(s.results) != 2 {
		t.Fatal("offline results lost")
	}
	err := s.drainResults(func(jobRes) error { return errors.New("disconnected") })
	if err == nil || len(s.results) != 2 {
		t.Fatal("failed flush consumed result")
	}
	var got []string
	err = s.drainResults(func(res jobRes) error { got = append(got, res.ID); return nil })
	if err != nil || len(s.results) != 0 || strings.Join(got, ",") != "first,second" {
		t.Fatalf("bad replay: %v %v", got, err)
	}
}

func TestTerminalResultWaitsForSpace(t *testing.T) {
	s := &session{}
	for i := 0; i < maxJobs; i++ {
		s.sendChunk("job", "out", "x")
	}
	s.sendChunk("job", "out", "lost")
	done := make(chan struct{})
	go func() { s.sendRes("job", true, 0, "", "", ""); close(done) }()
	select {
	case <-done:
		t.Fatal("terminal result discarded instead of waiting")
	case <-time.After(20 * time.Millisecond):
	}
	s.mu.Lock()
	_ = s.drainResults(func(jobRes) error { return nil })
	s.mu.Unlock()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("producer did not wake")
	}
	if len(s.results) != 1 || s.results[0].T != "res" || !s.results[0].Truncated {
		t.Fatalf("missing terminal or loss marker: %+v", s.results)
	}
}

func TestAutoUpdateWaitsForJobsAndUndeliveredResults(t *testing.T) {
	s := &session{jobs: newJobHub()}
	if !s.idleForUpdate() {
		t.Fatal("fresh connector should be idle")
	}
	s.jobs.live["job"] = &liveJob{id: "job", cancel: make(chan struct{})}
	if s.idleForUpdate() {
		t.Fatal("updating would abandon a job")
	}
	delete(s.jobs.live, "job")
	s.results = []jobRes{{ID: "job"}}
	if s.idleForUpdate() {
		t.Fatal("updating would lose a result")
	}
}

func TestConcurrentCancellationIsIdempotent(t *testing.T) {
	h := newJobHub()
	h.live["job"] = &liveJob{id: "job", cancel: make(chan struct{})}
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); h.cancel("job") }()
	}
	wg.Wait()
	select {
	case <-h.live["job"].cancel:
	default:
		t.Fatal("not cancelled")
	}
}

func TestRejectedQueueEntryDoesNotLeakLiveSlot(t *testing.T) {
	h := newJobHub()
	h.started = true // no workers: deterministically fill the queue
	for i := 0; i < maxQueue; i++ {
		h.queue <- map[string]any{}
	}
	h.handle(&session{}, map[string]any{"id": "new", "op": "exec"})
	if len(h.live) != 0 {
		t.Fatal("leaked live slot")
	}
}

func TestCancelledQueuedWriteDoesNotMutate(t *testing.T) {
	p := filepath.Join(t.TempDir(), "file")
	if err := os.WriteFile(p, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	h := newJobHub()
	h.live["job"] = &liveJob{id: "job", cancel: make(chan struct{})}
	h.cancel("job")
	h.run(&session{}, map[string]any{"id": "job", "op": "write", "path": p, "content": "wrong"})
	data, _ := os.ReadFile(p)
	if string(data) != "original" {
		t.Fatal("cancelled write executed")
	}
}

func TestClipDoesNotSplitUTF8(t *testing.T) {
	s := strings.Repeat("€", outCap)
	got := clipOut(s)
	if len(got) > outCap || !utf8.ValidString(got) {
		t.Fatal("invalid UTF-8 clipping")
	}
}

func TestWriteByteLimitPreservesOriginal(t *testing.T) {
	p := filepath.Join(t.TempDir(), "file")
	if err := os.WriteFile(p, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, content := range []string{strings.Repeat("€", 140000), "a\x00b"} {
		if err := writeFileAtomic(p, content); err == nil {
			t.Fatal("invalid content accepted")
		}
		data, _ := os.ReadFile(p)
		if string(data) != "original" {
			t.Fatal("original overwritten")
		}
	}
}
