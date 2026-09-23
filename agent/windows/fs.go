package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	slashpath "path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

var searchSkipDirs = map[string]struct{}{
	"node_modules": {}, ".git": {}, ".hg": {}, ".svn": {},
	"venv": {}, ".venv": {}, "env": {}, ".tox": {},
	"dist": {}, "build": {}, "out": {}, "target": {},
	"__pycache__": {}, ".mypy_cache": {}, ".pytest_cache": {}, ".ruff_cache": {},
	"vendor": {}, ".next": {}, "coverage": {},
	".gradle": {}, ".idea": {}, ".cache": {},
}

func skipSearchDir(root, p string, d os.DirEntry) bool {
	if !d.IsDir() {
		return false
	}
	if _, ok := searchSkipDirs[d.Name()]; !ok {
		return false
	}
	return filepath.Clean(p) != filepath.Clean(root)
}

// Whole-file edit ceiling for replace. Only old/new travel over the socket
// (never the file), so this is a RAM guard, not a transport limit. Keep in
// sync with _REPLACE_FILE_MAX in app/workspace.py and EDIT_FILE_MAX in
// agent/src/fuse-agent.c.
const editFileMax = 64 << 20

// write op content ceiling (the server pre-checks 400000; transport-bounded).
const writeFileMax = 400000

func userHome() string {
	if h := os.Getenv("USERPROFILE"); h != "" {
		return h
	}
	if h, err := os.UserHomeDir(); err == nil && h != "" {
		return h
	}
	if h := os.Getenv("HOME"); h != "" {
		return h
	}
	return "."
}

func expandHome(p string) string {
	if p == "" || p == "~" || p == "$HOME" {
		return userHome()
	}
	home := userHome()
	switch {
	case strings.HasPrefix(p, `~\`) || strings.HasPrefix(p, "~/"):
		return filepath.Join(home, p[2:])
	case strings.HasPrefix(p, "$HOME/") || strings.HasPrefix(p, `$HOME\`):
		return filepath.Join(home, p[6:])
	case strings.EqualFold(p, "%USERPROFILE%"):
		return home
	case len(p) > 13 && strings.EqualFold(p[:13], "%USERPROFILE%"):
		rest := p[13:]
		rest = strings.TrimPrefix(rest, `\`)
		rest = strings.TrimPrefix(rest, `/`)
		if rest == "" {
			return home
		}
		return filepath.Join(home, rest)
	default:
		return p
	}
}

func parentOf(p string) string {
	clean := filepath.Clean(p)
	vol := filepath.VolumeName(clean)
	rest := strings.TrimPrefix(clean, vol)
	rest = strings.Trim(rest, `\/`)
	if rest == "" {
		return ""
	}
	dir := filepath.Dir(clean)
	if dir == clean {
		return ""
	}
	return dir
}

func readFileSlice(path string, offset, limit int) (string, error) {
	path = expandHome(path)
	if offset < 1 {
		offset = 1
	}
	if limit < 1 {
		limit = 2000
	}
	if limit > 2000 {
		limit = 2000
	}
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	r := bufio.NewReaderSize(f, 8192)
	var out strings.Builder
	line, scanned := 1, 0
	for line < offset+limit {
		part, readErr := r.ReadSlice('\n')
		scanned += len(part)
		if scanned > 64<<20 {
			return "", fmt.Errorf("read scan exceeds 64 MiB; use grep")
		}
		if line >= offset {
			if out.Len()+len(part) > 65536 {
				return "", fmt.Errorf("read output exceeds 65536 bytes; use a smaller limit or grep")
			}
			out.Write(part)
		}
		if len(part) > 0 && part[len(part)-1] == '\n' {
			line++
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil && readErr != bufio.ErrBufferFull {
			return "", readErr
		}
	}
	text := out.String()
	if !utf8.ValidString(text) || strings.ContainsRune(text, 0) {
		return "", fmt.Errorf("file is not UTF-8 text")
	}
	return text, nil
}

func writeFileAtomicLimit(path, content string, max int) error {
	if len(content) > max || !utf8.ValidString(content) || strings.ContainsRune(content, 0) {
		return fmt.Errorf("content must be UTF-8 text without NUL, maximum %d bytes", max)
	}
	path = expandHome(path)
	if st, err := os.Lstat(path); err == nil {
		if !st.Mode().IsRegular() {
			return fmt.Errorf("write target must be a regular file")
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".fuse-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
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

func writeFileAtomic(path, content string) error {
	return writeFileAtomicLimit(path, content, writeFileMax)
}

func listDir(path string) (string, error) {
	path = expandHome(path)
	dir, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer dir.Close()
	ents, err := dir.ReadDir(400)
	if err != nil && err != io.EOF {
		return "", err
	}
	names := make([]string, 0, len(ents))
	for _, e := range ents {
		if len(names) >= 400 {
			break
		}
		name := e.Name()
		if name == "." || name == ".." {
			continue
		}
		if e.IsDir() {
			name += "/"
		} else if e.Type()&os.ModeSymlink != 0 {
			if info, err := os.Stat(filepath.Join(path, e.Name())); err == nil && info.IsDir() {
				name += "/"
			}
		}
		names = append(names, name)
	}
	sort.Strings(names)
	return strings.Join(names, "\n") + newlineIf(len(names) > 0), nil
}

func newlineIf(ok bool) string {
	if ok {
		return "\n"
	}
	return ""
}

type browseEntry struct {
	Name string `json:"name"`
	Kind string `json:"kind"`
}

type browseResult struct {
	Path    string        `json:"path"`
	Parent  string        `json:"parent"`
	Home    string        `json:"home"`
	Entries []browseEntry `json:"entries"`
}

func browseDir(path string) (string, error) {
	resolved := expandHome(path)
	st, err := os.Stat(resolved)
	if err != nil || !st.IsDir() {
		if err == nil {
			err = fmt.Errorf("not a directory")
		}
		return "", err
	}
	abs, err := filepath.Abs(resolved)
	if err == nil {
		resolved = abs
	}
	ents, err := os.ReadDir(resolved)
	if err != nil {
		return "", err
	}
	out := browseResult{
		Path:    resolved,
		Parent:  parentOf(resolved),
		Home:    userHome(),
		Entries: make([]browseEntry, 0, 64),
	}
	n := 0
	for _, e := range ents {
		if e.Name() == "." || e.Name() == ".." {
			continue
		}
		if strings.ContainsAny(e.Name(), `/\`) {
			continue
		}
		kind := "file"
		if e.IsDir() {
			kind = "dir"
		} else if e.Type()&os.ModeSymlink != 0 {
			if info, err := os.Stat(filepath.Join(resolved, e.Name())); err == nil && info.IsDir() {
				kind = "dir"
			}
		}
		out.Entries = append(out.Entries, browseEntry{Name: e.Name(), Kind: kind})
		n++
		if n >= 400 {
			break
		}
	}
	js, err := json.Marshal(out)
	if err != nil {
		return "", err
	}
	return string(js), nil
}

func globOK(s string) bool {
	n := len(s)
	if n == 0 || n > 200 {
		return false
	}
	if s[0] == '/' || s[0] == '~' || s[0] == '\\' || (n >= 2 && s[0] == '.' && s[1] == '.') {
		return false
	}
	if strings.Contains(s, "/..") || strings.Contains(s, `\..`) {
		return false
	}
	for i := 0; i < n; i++ {
		c := s[i]
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9':
		case strings.ContainsRune("_.*?/[]{}!,-\\", rune(c)):
		default:
			return false
		}
	}
	return true
}

func globDir(root, pattern string) (string, error) {
	root = expandHome(root)
	if !globOK(pattern) {
		return "", fmt.Errorf("invalid pattern")
	}
	var matches []string
	pat := filepath.ToSlash(pattern)
	err := filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.Name() == "." || d.Name() == ".." {
			return nil
		}
		if skipSearchDir(root, p, d) {
			return filepath.SkipDir
		}
		rel, err := filepath.Rel(root, p)
		if err != nil {
			return nil
		}
		relSlash := filepath.ToSlash(rel)
		if d.IsDir() {
			return nil
		}
		ok, matchErr := pathMatch(pat, relSlash)
		if matchErr != nil {
			return matchErr
		}
		if ok {
			matches = append(matches, p)
			if len(matches) >= 400 {
				return io.EOF
			}
		}
		return nil
	})
	if err != nil && err != io.EOF {
		return "", err
	}
	return strings.Join(matches, "\n") + newlineIf(len(matches) > 0), nil
}

func pathMatch(pattern, rel string) (bool, error) {
	pattern = strings.ReplaceAll(pattern, `\`, "/")
	rel = strings.ReplaceAll(rel, `\`, "/")
	if strings.ContainsAny(pattern, "{}") {
		return false, fmt.Errorf("brace globs are not supported by this connector")
	}
	if !strings.Contains(pattern, "/") {
		return slashpath.Match(pattern, slashpath.Base(rel))
	}
	parts, names := strings.Split(pattern, "/"), strings.Split(rel, "/")
	type state struct{ i, j int }
	seen := map[state]bool{}
	var match func(int, int) (bool, error)
	match = func(i, j int) (bool, error) {
		key := state{i, j}
		if seen[key] {
			return false, nil
		}
		seen[key] = true
		if i == len(parts) {
			return j == len(names), nil
		}
		if parts[i] == "**" {
			if ok, err := match(i+1, j); ok || err != nil {
				return ok, err
			}
			if j < len(names) {
				return match(i, j+1)
			}
			return false, nil
		}
		if j == len(names) {
			return false, nil
		}
		ok, err := slashpath.Match(parts[i], names[j])
		if err != nil || !ok {
			return false, err
		}
		return match(i+1, j+1)
	}
	return match(0, 0)
}

func grepDir(root, pattern string) (string, error) {
	return grepDirOptions(root, pattern, "", 200)
}

func grepDirOptions(root, pattern, include string, maxMatches int) (string, error) {
	if pattern == "" || len(pattern) > 200 {
		return "", fmt.Errorf("invalid pattern or path")
	}
	re, err := regexp.Compile(pattern)
	if err != nil {
		return "", fmt.Errorf("invalid pattern")
	}
	root = expandHome(root)
	if maxMatches < 1 || maxMatches > 200 {
		maxMatches = 200
	}
	if include != "" {
		if _, err := pathMatch(include, ""); err != nil {
			return "", err
		}
	}
	var b strings.Builder
	n := 0
	walkErr := filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if skipSearchDir(root, p, d) {
				return filepath.SkipDir
			}
			return nil
		}
		if d.Type()&os.ModeSymlink != 0 {
			return nil
		}
		if include != "" {
			rel, err := filepath.Rel(root, p)
			if err != nil {
				return err
			}
			if rel == "." {
				rel = d.Name()
			}
			ok, err := pathMatch(include, filepath.ToSlash(rel))
			if err != nil {
				return err
			}
			if !ok {
				return nil
			}
		}
		info, err := d.Info()
		if err != nil || !info.Mode().IsRegular() || info.Size() > 1<<20 {
			return nil
		}
		data, err := os.ReadFile(p)
		if err != nil || !utf8.Valid(data) {
			return nil
		}
		for i, line := range strings.Split(string(data), "\n") {
			if re.MatchString(line) {
				fmt.Fprintf(&b, "%s:%d:%s\n", p, i+1, line)
				n++
				if n >= maxMatches || b.Len() >= 65536 {
					return io.EOF
				}
			}
		}
		return nil
	})
	if walkErr != nil && walkErr != io.EOF {
		return "", walkErr
	}
	return b.String(), nil
}

func replaceFile(path, old, newv string, all bool) (string, error) {
	if old == "" {
		return "", fmt.Errorf("old_string required")
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
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, editFileMax+1))
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
	oldUse, newUse := old, newv
	count := strings.Count(text, oldUse)
	if count == 0 && strings.Contains(text, "\r\n") && !strings.ContainsRune(old, '\r') && strings.ContainsRune(old, '\n') {
		// read_file hides carriage returns, so an LF-only old_string can never
		// match a CRLF file byte-for-byte. Convert LF to CRLF: deterministic
		// line-ending repair, never a fuzzy match.
		crlfOld := strings.ReplaceAll(old, "\n", "\r\n")
		crlfNew := strings.ReplaceAll(newv, "\n", "\r\n")
		if c := strings.Count(text, crlfOld); c > 0 {
			oldUse, newUse = crlfOld, crlfNew
			count = c
		}
	}
	if count == 0 {
		return "", fmt.Errorf("old_string not found")
	}
	if count > 1 && !all {
		return "", fmt.Errorf("old_string is not unique")
	}
	var next string
	if all {
		next = strings.ReplaceAll(text, oldUse, newUse)
	} else {
		next = strings.Replace(text, oldUse, newUse, 1)
		count = 1
	}
	if len(next) > editFileMax {
		return "", fmt.Errorf("replacement too large (maximum %d bytes); apply the change with run_terminal_command", editFileMax)
	}
	after, err := os.Lstat(path)
	if err != nil {
		return "", err
	}
	if !os.SameFile(before, after) || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return "", fmt.Errorf("file changed during edit; read it again")
	}
	if err := writeFileAtomicLimit(path, next, editFileMax); err != nil {
		return "", err
	}
	return fmt.Sprintf("%d replacement(s)", count), nil
}
