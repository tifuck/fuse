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

func TestGrepLiteralIncludeNarrowsToFileInRoot(t *testing.T) {
	dir := t.TempDir()
	for _, rel := range []string{"main.py", filepath.Join("web", "main.py")} {
		p := filepath.Join(dir, rel)
		if err := os.MkdirAll(filepath.Dir(p), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte("hit\n"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	out, err := grepDirOptions(dir, "hit", "main.py", 10)
	if err != nil || strings.Count(out, "hit") != 1 || strings.Contains(out, "web") {
		t.Fatalf("literal include should search only the file in root: %q %v", out, err)
	}
	// Not directly in root: every file of that name below it.
	out, err = grepDirOptions(filepath.Join(dir), "hit", "*.py", 10)
	if err != nil || strings.Count(out, "hit") != 2 {
		t.Fatalf("%q %v", out, err)
	}
	out, err = grepDirOptions(dir, "hit", "web/main.py", 10)
	if err != nil || !strings.Contains(out, filepath.Join("web", "main.py")) || strings.Count(out, "hit") != 1 {
		t.Fatalf("include relative to root: %q %v", out, err)
	}
}

func TestGrepIncludeRejectsEscapes(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "secret.txt"), []byte("hit\n"), 0600); err != nil {
		t.Fatal(err)
	}
	parent := filepath.Dir(dir)
	if err := os.WriteFile(filepath.Join(parent, "outside.txt"), []byte("hit\n"), 0600); err != nil {
		t.Fatal(err)
	}
	defer os.Remove(filepath.Join(parent, "outside.txt"))
	// Literal-include narrowing must not walk outside the searched root.
	for _, include := range []string{
		`..\outside.txt`, "../outside.txt", "/etc/passwd", `C:\secret.txt`, "~/.ssh/id_rsa", ".",
	} {
		if out, err := grepDirOptions(dir, "hit", include, 10); err == nil {
			t.Fatalf("include %q escaped the root: %q", include, out)
		}
	}
	// An in-root literal still narrows to that file.
	out, err := grepDirOptions(dir, "hit", "secret.txt", 10)
	if err != nil || !strings.Contains(out, "secret.txt") {
		t.Fatalf("in-root include broken: %q %v", out, err)
	}
}

func TestWriteInPlaceRestoresOnWriteFailure(t *testing.T) {
	p := filepath.Join(t.TempDir(), "f.txt")
	if err := os.WriteFile(p, []byte("original content"), 0600); err != nil {
		t.Fatal(err)
	}
	saved := openInPlaceFile
	openInPlaceFile = func(string) (inPlaceFile, error) { return failingFile{}, nil }
	defer func() { openInPlaceFile = saved }()
	if err := writeInPlace(p, "new body that is longer", 400000); err == nil {
		t.Fatal("expected write failure")
	}
	if b, _ := os.ReadFile(p); string(b) != "original content" {
		t.Fatalf("original destroyed by failed fallback write: %q", b)
	}
}

type failingFile struct{}

func (failingFile) WriteString(string) (int, error)    { return 0, os.ErrClosed }
func (failingFile) WriteAt([]byte, int64) (int, error) { return 0, os.ErrClosed }
func (failingFile) Truncate(int64) error               { return nil }
func (failingFile) Sync() error                        { return nil }
func (failingFile) Close() error                       { return nil }

func TestWriteTruncatesTailOfShorterContent(t *testing.T) {
	p := filepath.Join(t.TempDir(), "f.txt")
	if err := os.WriteFile(p, []byte("long original body"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := writeInPlace(p, "short", 400000); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(p); string(b) != "short" {
		t.Fatalf("stale tail kept: %q", b)
	}
}

func TestGrepSearchesSymlinkNamedAsRoot(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "real.py")
	if err := os.WriteFile(target, []byte("hit\n"), 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "link.py")
	if err := os.Symlink(target, link); err != nil {
		t.Skip("symlinks unavailable:", err)
	}
	out, err := grepDirOptions(link, "hit", "", 10)
	if err != nil || !strings.Contains(out, "link.py:1:hit") {
		t.Fatalf("symlink named as path not searched: %q %v", out, err)
	}
	// A walk still does not follow links.
	out, _ = grepDirOptions(dir, "hit", "", 10)
	if strings.Contains(out, "link.py") {
		t.Fatalf("walk followed a symlink: %q", out)
	}
}
