package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestExpandHome(t *testing.T) {
	home := userHome()
	if expandHome("") != home || expandHome("~") != home || expandHome("$HOME") != home {
		t.Fatalf("home %q vs %q", expandHome("~"), home)
	}
	slash := expandHome("~/src")
	if !strings.HasSuffix(slash, "src") {
		t.Fatalf("~/src -> %q", slash)
	}
	if expandHome("/abs") != "/abs" {
		t.Fatal("abs")
	}
}

func TestReplaceAndBrowse(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "a.txt")
	if err := os.WriteFile(path, []byte("one two one"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := replaceFile(path, "one", "x", false); err == nil {
		t.Fatal("expected not unique")
	}
	msg, err := replaceFile(path, "one", "x", true)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(msg, "2") {
		t.Fatalf("msg %q", msg)
	}
	body, _ := os.ReadFile(path)
	if string(body) != "x two x" {
		t.Fatalf("body %q", body)
	}
	js, err := browseDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var br browseResult
	if err := json.Unmarshal([]byte(js), &br); err != nil {
		t.Fatal(err)
	}
	if br.Path == "" || br.Home == "" {
		t.Fatalf("browse %+v", br)
	}
	found := false
	for _, e := range br.Entries {
		if e.Name == "a.txt" && e.Kind == "file" {
			found = true
		}
	}
	if !found {
		t.Fatalf("entries %+v", br.Entries)
	}
}

func TestGlobOK(t *testing.T) {
	if globOK("") || globOK("/etc") || globOK("..") || globOK("../x") {
		t.Fatal("rejected patterns accepted")
	}
	if !globOK("*.go") || !globOK(`src\*.c`) || !globOK("**/*.md") {
		t.Fatal("valid patterns rejected")
	}
}

func TestGrepAndRead(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "n.txt")
	os.WriteFile(p, []byte("alpha\nbeta\nalpha\n"), 0644)
	out, err := grepDir(dir, "alpha")
	if err != nil || strings.Count(out, "alpha") != 2 {
		t.Fatalf("grep %q %v", out, err)
	}
	s, err := readFileSlice(p, 2, 1)
	if err != nil || strings.TrimSpace(s) != "beta" {
		t.Fatalf("read %q %v", s, err)
	}
}

func TestGrepSkipsVendorDirs(t *testing.T) {
	dir := t.TempDir()
	os.Mkdir(filepath.Join(dir, "node_modules"), 0755)
	os.WriteFile(filepath.Join(dir, "node_modules", "x.js"), []byte("alpha\n"), 0644)
	os.WriteFile(filepath.Join(dir, "app.js"), []byte("alpha\n"), 0644)
	out, err := grepDir(dir, "alpha")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(out, "node_modules") {
		t.Fatalf("walked node_modules: %q", out)
	}
	if !strings.Contains(out, "app.js") {
		t.Fatalf("missed app.js: %q", out)
	}
}

func TestReplaceCRLFRepair(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "crlf.txt")
	if err := os.WriteFile(path, []byte("alpha\r\nbeta\r\n"), 0644); err != nil {
		t.Fatal(err)
	}
	// read_file hides carriage returns, so LF-only old_string must still work.
	if _, err := replaceFile(path, "alpha\nbeta\n", "one\ntwo\n", false); err != nil {
		t.Fatal(err)
	}
	body, _ := os.ReadFile(path)
	if string(body) != "one\r\ntwo\r\n" {
		t.Fatalf("body %q", body)
	}
	// An old_string that already carries CR is used verbatim.
	if _, err := replaceFile(path, "one\r\ntwo", "x\r\ny", false); err != nil {
		t.Fatal(err)
	}
	body, _ = os.ReadFile(path)
	if string(body) != "x\r\ny\r\n" {
		t.Fatalf("body %q", body)
	}
}

func TestReplaceTooLargeNamesLimit(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "big.txt")
	if err := os.WriteFile(path, []byte(strings.Repeat("x", editFileMax+1)), 0644); err != nil {
		t.Fatal(err)
	}
	_, err := replaceFile(path, "x", "y", false)
	if err == nil || !strings.Contains(err.Error(), "file too large to edit") {
		t.Fatalf("err %v", err)
	}
	// A file inside the edit ceiling is editable.
	path2 := filepath.Join(dir, "mid.txt")
	if err := os.WriteFile(path2, []byte(strings.Repeat("x", 400001)), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := replaceFile(path2, strings.Repeat("x", 400000), "z", false); err != nil {
		t.Fatal(err)
	}
	body, _ := os.ReadFile(path2)
	if string(body) != "zx" {
		t.Fatalf("body %q", body)
	}
}

func TestListDirMarksDirectories(t *testing.T) {
	dir := t.TempDir()
	os.Mkdir(filepath.Join(dir, "src"), 0755)
	os.WriteFile(filepath.Join(dir, "main.py"), []byte("x\n"), 0644)
	out, err := listDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "src/") {
		t.Fatalf("missing dir marker: %q", out)
	}
	if !strings.Contains(out, "main.py") {
		t.Fatalf("missing file: %q", out)
	}
}

func TestWalkErrorSkipsUnreadableEntriesBelowTheRoot(t *testing.T) {
	root := t.TempDir()
	if err := walkError(root, filepath.Join(root, "locked"), os.ErrPermission); err != nil {
		t.Fatalf("an unreadable subdirectory must be skipped, got %v", err)
	}
	if err := walkError(root, root, os.ErrPermission); err == nil {
		t.Fatal("an unreadable search root must fail the search")
	}
}
