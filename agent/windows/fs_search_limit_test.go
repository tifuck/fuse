package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func searchTree(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	for i, name := range []string{"a.js", "b.js", filepath.Join("sub", "c.js")} {
		p := filepath.Join(dir, name)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(strings.Repeat("x\n", i)+"needle\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

func TestGrepWithinBudgetIsComplete(t *testing.T) {
	dir := searchTree(t)
	out, partial, err := grepDirSearch(dir, "needle", "", 50, grepOpts{limit: searchBudget(20, nil)})
	if err != nil || partial {
		t.Fatalf("err=%v partial=%v", err, partial)
	}
	if strings.Count(out, "needle") != 3 {
		t.Fatalf("want 3 matches, got %q", out)
	}
}

func TestGrepPastItsDeadlineStopsWithPartialResults(t *testing.T) {
	dir := searchTree(t)
	out, partial, err := grepDirSearch(dir, "needle", "", 50, grepOpts{limit: searchLimit{deadline: time.Now().Add(-time.Second)}})
	if err != nil || !partial {
		t.Fatalf("a spent budget must stop the walk as partial, not fail: err=%v partial=%v", err, partial)
	}
	if out != "" {
		t.Fatalf("nothing is searched past the deadline, got %q", out)
	}
}

func TestGrepStopsWhenTheJobIsCancelled(t *testing.T) {
	dir := searchTree(t)
	cancel := make(chan struct{})
	close(cancel)
	_, partial, err := grepDirSearch(dir, "needle", "", 50, grepOpts{limit: searchLimit{cancel: cancel}})
	if err != nil || !partial {
		t.Fatalf("a cancelled job must stop its walk: err=%v partial=%v", err, partial)
	}
}

func TestGrepErrorsStayErrorsUnderALimit(t *testing.T) {
	dir := searchTree(t)
	if _, partial, err := grepDirSearch(dir, "(", "", 50, grepOpts{limit: searchBudget(20, nil)}); err == nil || partial {
		t.Fatalf("an invalid pattern is an error, not a partial result: err=%v partial=%v", err, partial)
	}
}

func TestGlobPastItsDeadlineStopsWithPartialResults(t *testing.T) {
	dir := searchTree(t)
	out, partial, err := globDirSearch(dir, "**/*.js", "path", searchBudget(20, nil))
	if err != nil || partial || strings.Count(out, ".js") != 3 {
		t.Fatalf("within budget: out=%q partial=%v err=%v", out, partial, err)
	}
	out, partial, err = globDirSearch(dir, "**/*.js", "path", searchLimit{deadline: time.Now().Add(-time.Second)})
	if err != nil || !partial || out != "" {
		t.Fatalf("past the deadline: out=%q partial=%v err=%v", out, partial, err)
	}
}

func TestSearchBudgetDefaultsWhenTheServerSendsNone(t *testing.T) {
	for _, sent := range []int{0, -5, 100000} {
		left := time.Until(searchBudget(sent, nil).deadline)
		if left < 19*time.Second || left > 20*time.Second {
			t.Fatalf("timeout_s=%d: want the 20 s default, got %v", sent, left)
		}
	}
	if left := time.Until(searchBudget(5, nil).deadline); left < 4*time.Second || left > 5*time.Second {
		t.Fatalf("timeout_s=5: got %v", left)
	}
}
