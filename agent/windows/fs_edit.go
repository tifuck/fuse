// Filesystem tools, write side: atomic writes and exact in-place edits.
package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"unicode/utf8"
)

// Whole-file edit ceiling for replace. Only old/new travel over the socket
// (never the file), so this is a RAM guard, not a transport limit. Keep in
// sync with _REPLACE_FILE_MAX in app/workspace.py and EDIT_FILE_MAX in
// agent/src/agent.h.
const editFileMax = 64 << 20

// write op content ceiling (the server pre-checks 400000; transport-bounded).
const writeFileMax = 400000

func writeFileAtomicLimit(path, content string, max int) error {
	if len(content) > max || !utf8.ValidString(content) || strings.ContainsRune(content, 0) {
		return fmt.Errorf("content must be UTF-8 text without NUL, maximum %d bytes", max)
	}
	path = expandHome(path)
	// os.CreateTemp creates 0600: keep an existing file's mode, and give a
	// new file the conventional 0644 instead.
	mode := os.FileMode(0644)
	exists := false
	if st, err := os.Lstat(path); err == nil {
		if !st.Mode().IsRegular() {
			return fmt.Errorf("write target must be a regular file")
		}
		mode = st.Mode().Perm()
		exists = true
	} else if !os.IsNotExist(err) {
		return err
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".fuse-*.tmp")
	if err != nil {
		// Directory not writable but the file is: rewrite the file itself.
		if exists && os.IsPermission(err) {
			return writeInPlace(path, content)
		}
		return err
	}
	tmpName := tmp.Name()
	if err := tmp.Chmod(mode); err != nil && runtime.GOOS != "windows" {
		tmp.Close()
		os.Remove(tmpName)
		return err
	}
	if _, err := tmp.WriteString(content); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmpName)
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		os.Remove(tmpName)
		return err
	}
	return nil
}

// writeInPlace rewrites an existing regular file without replacing it (same
// file identity, attributes and ACLs). Not atomic; only a fallback.
func writeInPlace(path, content string) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_TRUNC, 0)
	if err != nil {
		return err
	}
	if _, err := f.WriteString(content); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}

func writeFileAtomic(path, content string) error {
	return writeFileAtomicLimit(path, content, writeFileMax)
}

// fileEdit is one exact replacement; replaceEdits applies a list in order to
// the same text and publishes once — all or nothing.
type fileEdit struct {
	Old string
	New string
	All bool
}

func replaceFile(path, old, newv string, all bool) (string, error) {
	return replaceEdits(path, []fileEdit{{old, newv, all}})
}

// applyEdit replaces old with newv in text. LF-only strings against a CRLF
// text are converted to CRLF (read_file hides carriage returns) —
// deterministic line-ending repair, never a fuzzy match.
func applyEdit(text string, e fileEdit) (string, int, error) {
	if e.Old == "" {
		return "", 0, fmt.Errorf("old_string required")
	}
	if e.Old == e.New {
		return "", 0, fmt.Errorf("old_string and new_string are identical")
	}
	oldUse, newUse := e.Old, e.New
	count := strings.Count(text, oldUse)
	if count == 0 && strings.Contains(text, "\r\n") && !strings.ContainsRune(e.Old, '\r') && strings.ContainsRune(e.Old, '\n') {
		crlfOld := strings.ReplaceAll(e.Old, "\n", "\r\n")
		crlfNew := strings.ReplaceAll(e.New, "\n", "\r\n")
		if c := strings.Count(text, crlfOld); c > 0 {
			oldUse, newUse = crlfOld, crlfNew
			count = c
		}
	}
	if count == 0 {
		return "", 0, fmt.Errorf("old_string not found")
	}
	if count > 1 && !e.All {
		return "", 0, fmt.Errorf("old_string is not unique (%d occurrences)", count)
	}
	if e.All {
		return strings.ReplaceAll(text, oldUse, newUse), count, nil
	}
	return strings.Replace(text, oldUse, newUse, 1), 1, nil
}

func replaceEdits(path string, edits []fileEdit) (string, error) {
	if len(edits) < 1 || len(edits) > 100 {
		return "", fmt.Errorf("edits must contain 1 to 100 items")
	}
	path = expandHome(path)
	before, err := os.Lstat(path)
	if err != nil {
		return "", err
	}
	if !before.Mode().IsRegular() {
		return "", fmt.Errorf("edit target must be a regular file")
	}
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	data, err := io.ReadAll(io.LimitReader(f, editFileMax+1))
	// Close before publishing: Go opens files on Windows without
	// FILE_SHARE_DELETE, so renaming the new content over a file this
	// process still holds open fails with "Access denied".
	f.Close()
	if err != nil {
		return "", err
	}
	if len(data) > editFileMax {
		return "", fmt.Errorf("file too large to edit (maximum %d bytes); apply the change with run_terminal_command", editFileMax)
	}
	text := string(data)
	if !utf8.ValidString(text) || strings.ContainsRune(text, 0) {
		return "", fmt.Errorf("file is not UTF-8 text")
	}
	total := 0
	for i, e := range edits {
		next, count, err := applyEdit(text, e)
		if err != nil {
			if len(edits) > 1 {
				return "", fmt.Errorf("edit %d of %d: %v; no edits were applied", i+1, len(edits), err)
			}
			return "", err
		}
		if len(next) > editFileMax {
			return "", fmt.Errorf("replacement too large (maximum %d bytes); apply the change with run_terminal_command", editFileMax)
		}
		text = next
		total += count
	}
	after, err := os.Lstat(path)
	if err != nil {
		return "", err
	}
	if !os.SameFile(before, after) || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return "", fmt.Errorf("file changed during edit; read it again")
	}
	if err := writeFileAtomicLimit(path, text, editFileMax); err != nil {
		return "", err
	}
	return fmt.Sprintf("%d replacement(s)", total), nil
}
