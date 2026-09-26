package main

import (
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestExpandBraces(t *testing.T) {
	got := expandBraces("src/*.{ts,tsx}")
	if strings.Join(got, "|") != "src/*.ts|src/*.tsx" {
		t.Fatalf("got %v", got)
	}
	if got := expandBraces("{a,b}/{c,d}"); len(got) != 4 {
		t.Fatalf("nested/multiple groups: %v", got)
	}
	if got := expandBraces("plain"); len(got) != 1 || got[0] != "plain" {
		t.Fatalf("got %v", got)
	}
}

func TestGlobOKAcceptsRealNames(t *testing.T) {
	for _, p := range []string{"src/routes/**/+page.svelte", "node_modules/@scope/**", "docs/My File.md", "**/*.{py,js}"} {
		if !globOK(p) {
			t.Fatalf("%q should be allowed", p)
		}
	}
	for _, p := range []string{"a | wc", "$(id)", "/abs", "../up"} {
		if globOK(p) {
			t.Fatalf("%q should be refused", p)
		}
	}
}

func TestGlobSortsByPathAndModified(t *testing.T) {
	dir := t.TempDir()
	for i, name := range []string{"b.go", "a.go", "c.go"} {
		p := filepath.Join(dir, name)
		if err := os.WriteFile(p, []byte("x"), 0644); err != nil {
			t.Fatal(err)
		}
		ts := time.Now().Add(time.Duration(i) * time.Hour)
		os.Chtimes(p, ts, ts)
	}
	out, err := globDirSorted(dir, "*.{go,md}", "path")
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Fields(strings.ReplaceAll(out, dir+string(os.PathSeparator), "")); strings.Join(got, ",") != "a.go,b.go,c.go" {
		t.Fatalf("path order: %v", got)
	}
	out, _ = globDirSorted(dir, "*.go", "modified")
	if got := strings.Fields(strings.ReplaceAll(out, dir+string(os.PathSeparator), "")); strings.Join(got, ",") != "c.go,a.go,b.go" {
		t.Fatalf("modified order: %v", got)
	}
}

func TestReplaceEditsAllOrNothing(t *testing.T) {
	p := filepath.Join(t.TempDir(), "f.txt")
	os.WriteFile(p, []byte("alpha\nbeta\ngamma\n"), 0644)
	if _, err := replaceEdits(p, []fileEdit{{"alpha", "A", false}, {"missing", "x", false}}); err == nil ||
		!strings.Contains(err.Error(), "edit 2 of 2") {
		t.Fatalf("want edit 2 failure, got %v", err)
	}
	if b, _ := os.ReadFile(p); string(b) != "alpha\nbeta\ngamma\n" {
		t.Fatalf("file changed on failed batch: %q", b)
	}
	out, err := replaceEdits(p, []fileEdit{{"alpha", "A", false}, {"A\nbeta", "A\nB", false}})
	if err != nil || out != "2 replacement(s)" {
		t.Fatalf("%v %q", err, out)
	}
	if b, _ := os.ReadFile(p); string(b) != "A\nB\ngamma\n" {
		t.Fatalf("got %q", b)
	}
	if _, err := replaceEdits(p, []fileEdit{{"A", "A", false}}); err == nil || !strings.Contains(err.Error(), "identical") {
		t.Fatalf("identical edit accepted: %v", err)
	}
}

func TestGrepModesAndContext(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "a.txt"), []byte("one\nTwo\nthree\nfour\nfive\nsix\ntwo again\n"), 0644)
	os.WriteFile(filepath.Join(dir, "b.txt"), []byte("nothing\n"), 0644)
	out, err := grepDirMode(dir, "two", "", 50, grepOpts{ignoreCase: true, mode: "files"})
	if err != nil || strings.Count(out, "\n") != 1 || !strings.HasSuffix(strings.TrimSpace(out), "a.txt") {
		t.Fatalf("files mode: %v %q", err, out)
	}
	out, _ = grepDirMode(dir, "two", "", 50, grepOpts{ignoreCase: true, mode: "count"})
	if !strings.HasSuffix(strings.TrimSpace(out), "a.txt:2") {
		t.Fatalf("count mode: %q", out)
	}
	out, _ = grepDirMode(dir, "three", "", 50, grepOpts{context: 1})
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if len(lines) != 3 || !strings.Contains(lines[0], "-2-Two") || !strings.Contains(lines[1], ":3:three") || !strings.Contains(lines[2], "-4-four") {
		t.Fatalf("context: %q", out)
	}
	out, _ = grepDirMode(dir, "one|six", "", 50, grepOpts{context: 1})
	if !strings.Contains(out, "\n--\n") {
		t.Fatalf("separator missing between groups: %q", out)
	}
	long := strings.Repeat("a", 1500) + "|b"
	if _, err := grepDirMode(dir, long, "", 5, grepOpts{}); err != nil {
		t.Fatalf("patterns up to 2000 chars must be accepted: %v", err)
	}
}

func TestWriteKeepsModeAndCreatesParents(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "x", "y", "run.sh")
	if err := writeFileAtomic(p, "#!/bin/sh\n"); err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS == "windows" {
		return // Unix permission bits do not exist on Windows
	}
	if st, _ := os.Stat(p); st.Mode().Perm() != 0644 {
		t.Fatalf("new file mode %v", st.Mode().Perm())
	}
	os.Chmod(p, 0755)
	if err := writeFileAtomic(p, "#!/bin/sh\necho\n"); err != nil {
		t.Fatal(err)
	}
	if st, _ := os.Stat(p); st.Mode().Perm() != 0755 {
		t.Fatalf("existing mode lost: %v", st.Mode().Perm())
	}
}

func TestWriteFallsBackInPlaceWhenDirReadOnly(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root bypasses directory permissions")
	}
	dir := t.TempDir()
	p := filepath.Join(dir, "f.txt")
	os.WriteFile(p, []byte("old"), 0644)
	os.Chmod(dir, 0555)
	defer os.Chmod(dir, 0755)
	if err := writeFileAtomic(p, "new"); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(p); string(b) != "new" {
		t.Fatalf("got %q", b)
	}
}

func TestListTreeDepthAndSorting(t *testing.T) {
	dir := t.TempDir()
	for _, d := range []string{"src/deep/deeper", "node_modules/pkg"} {
		os.MkdirAll(filepath.Join(dir, d), 0755)
	}
	for _, f := range []string{"src/a.py", "src/deep/b.py", "src/deep/deeper/c.py", "node_modules/pkg/i.js", "z.txt"} {
		os.WriteFile(filepath.Join(dir, f), []byte("x"), 0644)
	}
	out, err := listTree(dir, 2)
	if err != nil {
		t.Fatal(err)
	}
	want := "node_modules/\nsrc/\nsrc/a.py\nsrc/deep/\nz.txt\n"
	if out != want {
		t.Fatalf("depth 2:\n%s\nwant:\n%s", out, want)
	}
	for i := 0; i < 600; i++ {
		os.WriteFile(filepath.Join(dir, "f"+strings.Repeat("0", 3-len(itoa(i)))+itoa(i)), nil, 0644)
	}
	out, _ = listTree(dir, 1)
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if lines[0] != "f000" || len(lines) != 603 {
		t.Fatalf("flat listing must be complete and sorted: first=%q n=%d", lines[0], len(lines))
	}
}

func itoa(i int) string { return strconv.Itoa(i) }
