package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReadBeyondOldPrefixCap(t *testing.T) {
	p := filepath.Join(t.TempDir(), "large.txt")
	if err := os.WriteFile(p, []byte(strings.Repeat("prefix\n", 100000)+"wanted\n"), 0600); err != nil {
		t.Fatal(err)
	}
	out, err := readFileSlice(p, 100001, 1)
	if err != nil || out != "wanted\n" {
		t.Fatalf("%q %v", out, err)
	}
}

func TestReadRejectsClippedLine(t *testing.T) {
	p := filepath.Join(t.TempDir(), "long.txt")
	if err := os.WriteFile(p, []byte(strings.Repeat("x", 70000)), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readFileSlice(p, 1, 1); err == nil {
		t.Fatal("silently clipped long line")
	}
}

func TestGlobRespectsFullPath(t *testing.T) {
	for _, tc := range []struct {
		pattern, path string
		want          bool
	}{
		{"src/**/*.go", "src/a/b/c.go", true},
		{"src/**/*.go", "src/c.go", true},
		{"src/**/*.go", "other/c.go", false},
		{"**/test/*.go", "a/b/test/c.go", true},
		{"**/test/*.go", "a/test/deep/c.go", false},
		{"*.go", "a/b/c.go", true},
	} {
		got, err := pathMatch(tc.pattern, tc.path)
		if err != nil || got != tc.want {
			t.Errorf("%s %s: %v %v", tc.pattern, tc.path, got, err)
		}
	}
}

func TestSearchMissingRootIsError(t *testing.T) {
	p := filepath.Join(t.TempDir(), "absent")
	if _, err := globDir(p, "*.go"); err == nil {
		t.Fatal("missing glob root returned no matches")
	}
	if _, err := grepDir(p, "foo"); err == nil {
		t.Fatal("missing grep root returned no matches")
	}
}

func TestGrepIncludeBeforeResultCap(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "a.txt"), []byte(strings.Repeat("hit\n", 250)), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "z.go"), []byte("hit\n"), 0600); err != nil {
		t.Fatal(err)
	}
	out, err := grepDirOptions(dir, "hit", "*.go", 1)
	if err != nil || !strings.Contains(out, "z.go:1:hit") || strings.Contains(out, "a.txt") {
		t.Fatalf("%s %v", out, err)
	}
}

func TestGrepSearchesLargeFileWhenNamed(t *testing.T) {
	dir := t.TempDir()
	big := filepath.Join(dir, "big.py")
	body := strings.Repeat("filler line\n", 200000) + "needle\n" // > 1 MB
	if err := os.WriteFile(big, []byte(body), 0600); err != nil {
		t.Fatal(err)
	}
	if out, _ := grepDirOptions(dir, "needle", "", 10); out != "" {
		t.Fatalf("unfiltered walk should skip files over 1 MB: %q", out)
	}
	if out, err := grepDirOptions(big, "needle", "", 10); err != nil || !strings.Contains(out, "needle") {
		t.Fatalf("named file not searched: %q %v", out, err)
	}
	if out, err := grepDirOptions(dir, "needle", "big.py", 10); err != nil || !strings.Contains(out, "needle") {
		t.Fatalf("include-selected file not searched: %q %v", out, err)
	}
}

func TestReplacePreservesCRLFAndRejectsBinary(t *testing.T) {
	p := filepath.Join(t.TempDir(), "file")
	if err := os.WriteFile(p, []byte("one\r\ntwo\r\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := replaceFile(p, "two", "three", false); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(p)
	if string(data) != "one\r\nthree\r\n" {
		t.Fatalf("%q", data)
	}
	if err := os.WriteFile(p, []byte("one\x00two"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := replaceFile(p, "one", "x", false); err == nil {
		t.Fatal("binary edit accepted")
	}
}
