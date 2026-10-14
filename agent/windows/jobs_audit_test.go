package main

import (
	"encoding/json"
	"errors"
	"fmt"
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

func TestKillOpIsUnknownAndCancelFrameStillWorks(t *testing.T) {
	// op=kill read its target from "id" - the message's own job id - so it
	// could only cancel itself and lied "cancelled". It must now fall through
	// to "unknown op"; real cancellation is the "cancel" frame (jobs.cancel).
	h := newJobHub()
	h.live["killer"] = &liveJob{id: "killer", cancel: make(chan struct{})}
	s := &session{}
	h.run(s, map[string]any{"id": "killer", "op": "kill", "target": "victim"})
	if len(s.results) != 1 || s.results[0].OK || s.results[0].Error != "unknown op" {
		t.Fatalf("kill op must report unknown op: %+v", s.results)
	}
	h.live["victim"] = &liveJob{id: "victim", cancel: make(chan struct{})}
	h.cancel("victim")
	select {
	case <-h.live["victim"].cancel:
	default:
		t.Fatal("cancel frame path broken")
	}
}

func TestTerminationConfirmedIsNotForced(t *testing.T) {
	s := &session{}
	s.sendResFull("a", true, 0, "ok", "", "", false, false)
	s.sendResFull("b", true, 0, "ok", "", "", false, true)
	if s.results[0].TerminationConfirmed || !s.results[1].TerminationConfirmed {
		t.Fatalf("confirmed flag ignored: %+v", s.results)
	}
}

func TestResultsSurviveOfflineAndFailedFlush(t *testing.T) {
	s := &session{}
	s.sendRes("first", true, 0, "retained", "", "")
	s.sendRes("second", true, 0, "next", "", "")
	if len(s.results) != 2 {
		t.Fatal("offline results lost")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
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

// Streamed output waits for room (as the C connector) instead of being
// dropped, and leaves streamReserve slots so terminal results still fit.
func TestChunksWaitForSpaceAndLeaveRoomForResults(t *testing.T) {
	s := &session{}
	forever := time.Now().Add(time.Hour)
	for i := 0; i < maxJobs-streamReserve; i++ {
		s.sendChunk("job", "out", "x", nil, forever)
	}
	chunked := make(chan struct{})
	go func() { s.sendChunk("job", "out", "held", nil, forever); close(chunked) }()
	select {
	case <-chunked:
		t.Fatal("chunk queued past the stream limit")
	case <-time.After(50 * time.Millisecond):
	}
	s.sendRes("other", true, 0, "", "", "") // reserve: must not block
	var sent []jobRes
	collect := func(res jobRes) error { sent = append(sent, res); return nil }
	s.mu.Lock()
	_ = s.drainResults(collect)
	s.mu.Unlock()
	select {
	case <-chunked:
	case <-time.After(time.Second):
		t.Fatal("held chunk did not resume")
	}
	s.mu.Lock()
	_ = s.drainResults(collect)
	s.mu.Unlock()
	last := sent[len(sent)-1]
	if len(sent) != maxJobs-streamReserve+2 || last.Data != "held" || sent[len(sent)-2].ID != "other" {
		t.Fatalf("held chunk lost or reordered: %d sent, last %+v", len(sent), last)
	}
}

// A chunk held past its deadline (or cancel) gives up and the job's result
// says so, rather than holding the job forever on a dead link.
func TestHeldChunkGivesUpAndMarksResult(t *testing.T) {
	s := &session{}
	forever := time.Now().Add(time.Hour)
	for i := 0; i < maxJobs-streamReserve; i++ {
		s.sendChunk("other", "out", "x", nil, forever)
	}
	s.sendChunk("job", "out", "late", nil, time.Now().Add(20*time.Millisecond))
	stop := make(chan struct{})
	close(stop)
	s.sendChunk("job", "out", "cancelled", stop, forever)
	s.sendRes("job", true, 0, "", "", "")
	last := s.results[len(s.results)-1]
	if last.T != "res" || !last.Truncated {
		t.Fatalf("missing loss marker: %+v", last)
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

func TestRefusedJobDoesNotLeakLiveSlot(t *testing.T) {
	h := newJobHub()
	for i := 0; i < maxJobs; i++ {
		h.live[fmt.Sprint(i)] = &liveJob{cancel: make(chan struct{})}
	}
	s := &session{}
	h.handle(s, map[string]any{"id": "new", "op": "exec"})
	if len(h.live) != maxJobs || h.live["new"] != nil || len(s.results) != 1 || s.results[0].Error == "" {
		t.Fatalf("over-limit job not refused cleanly: live=%d results=%+v", len(h.live), s.results)
	}
}

// Each admitted job runs at once: a fixed pool of 16 workers queued a quick
// read behind long commands.
func TestJobsRunConcurrentlyBeyondOldPool(t *testing.T) {
	h := newJobHub()
	s := &session{}
	p := filepath.Join(t.TempDir(), "f")
	if err := os.WriteFile(p, []byte("hi\n"), 0600); err != nil {
		t.Fatal(err)
	}
	// Hold 20 jobs in mutationMu (writes serialize on it) while a read runs.
	mutationMu.Lock()
	for i := 0; i < 20; i++ {
		h.handle(s, map[string]any{"id": fmt.Sprint("w", i), "op": "write", "path": p, "content": "x"})
	}
	h.handle(s, map[string]any{"id": "r", "op": "read", "path": p})
	deadline := time.Now().Add(2 * time.Second)
	for {
		s.mu.Lock()
		got := len(s.results) > 0 && s.results[0].ID == "r" && s.results[0].OK
		s.mu.Unlock()
		if got {
			break
		}
		if time.Now().After(deadline) {
			mutationMu.Unlock()
			t.Fatal("read waited behind blocked jobs")
		}
		time.Sleep(5 * time.Millisecond)
	}
	mutationMu.Unlock()
	for { // the writes finish before TempDir cleanup
		h.mu.Lock()
		n := len(h.live)
		h.mu.Unlock()
		if n == 0 {
			return
		}
		time.Sleep(5 * time.Millisecond)
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
