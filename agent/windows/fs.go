package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"os"
	slashpath "path"
	"path/filepath"
	"regexp"
	"runtime"
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

const binaryFileMax = 20 << 20
const binaryChunkMax = 32768

// readBinary returns an internal, byte-oriented result; text reads stay separate.
func readBinary(path string, offset, limit int) (map[string]any, error) {
	if offset < 0 || offset > binaryFileMax || limit < 1 || limit > binaryChunkMax {
		return nil, fmt.Errorf("invalid binary offset or limit")
	}
	path = expandHome(path)
	before, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if !before.Mode().IsRegular() || before.Size() > binaryFileMax {
		return nil, fmt.Errorf("binary source must be a regular file of at most 20 MiB")
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	opened, err := f.Stat()
	if err != nil || !os.SameFile(before, opened) {
		return nil, fmt.Errorf("binary source changed before read")
	}
	h := sha256.New()
	buf := make([]byte, binaryChunkMax)
	data := make([]byte, 0, limit)
	var size int64
	for {
		n, readErr := f.Read(buf)
		if size+int64(n) > binaryFileMax {
			return nil, fmt.Errorf("binary source exceeds 20 MiB")
		}
		h.Write(buf[:n])
		lo, hi := max(int64(offset), size), min(int64(offset+limit), size+int64(n))
		if lo < hi {
			data = append(data, buf[lo-size:hi-size]...)
		}
		size += int64(n)
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return nil, readErr
		}
	}
	after, err := f.Stat()
	current, pathErr := os.Stat(path)
	if err != nil || pathErr != nil || !os.SameFile(before, current) || before.Size() != size || after.Size() != size || !before.ModTime().Equal(after.ModTime()) {
		return nil, fmt.Errorf("binary source changed during read")
	}
	return map[string]any{"ok": true, "data": base64.StdEncoding.EncodeToString(data), "size": size,
		"identity": fmt.Sprintf("sha256:%x", h.Sum(nil)), "eof": int64(offset+len(data)) >= size}, nil
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

const listScanMax = 1500

func listDir(path string) (string, error) {
	return listTree(path, 1)
}

// listTree lists path to depth levels (1 = the directory itself). Paths are
// relative with '/' separators and a trailing '/' on directories; vendor and
// build directories are listed but never entered. Everything is collected
// (up to listScanMax) and sorted before the server trims to its page, so the
// page is the alphabetical start rather than an arbitrary readdir subset.
func listTree(path string, depth int) (string, error) {
	if depth < 1 {
		depth = 1
	}
	if depth > 5 {
		depth = 5
	}
	path = expandHome(path)
	var names []string
	type item struct {
		rel, full string
		level     int
	}
	queue := []item{{"", path, 1}}
	for len(queue) > 0 && len(names) <= listScanMax {
		cur := queue[0]
		queue = queue[1:]
		ents, err := os.ReadDir(cur.full)
		if err != nil {
			if cur.level == 1 {
				return "", err
			}
			continue
		}
		for _, e := range ents {
			name := e.Name()
			if name == "." || name == ".." {
				continue
			}
			isDir := e.IsDir()
			if !isDir && e.Type()&os.ModeSymlink != 0 {
				if info, err := os.Stat(filepath.Join(cur.full, name)); err == nil && info.IsDir() {
					isDir = true
				}
			}
			rel := cur.rel + name
			if isDir {
				names = append(names, rel+"/")
				_, skip := searchSkipDirs[name]
				if cur.level < depth && !skip && e.IsDir() {
					queue = append(queue, item{rel + "/", filepath.Join(cur.full, name), cur.level + 1})
				}
			} else {
				names = append(names, rel)
			}
			if len(names) > listScanMax {
				break
			}
		}
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
	// Patterns are matched in-process, never passed to a shell: refuse only
	// control characters and pipeline/quoting characters that signal a
	// shell command rather than a glob. +page.svelte, @scope, spaces are fine.
	for i := 0; i < n; i++ {
		c := s[i]
		if c < 32 || strings.ContainsRune("|;<>`$\"", rune(c)) {
			return false
		}
	}
	return true
}

// expandBraces turns one {a,b} group at a time into alternatives:
// "*.{ts,tsx}" -> ["*.ts", "*.tsx"]. Nested groups expand recursively.
func expandBraces(pattern string) []string {
	open := strings.IndexByte(pattern, '{')
	if open < 0 {
		return []string{pattern}
	}
	depth := 0
	closeAt := -1
	var parts []string
	start := open + 1
	for i := open; i < len(pattern); i++ {
		switch pattern[i] {
		case '{':
			depth++
		case '}':
			depth--
			if depth == 0 {
				closeAt = i
			}
		case ',':
			if depth == 1 {
				parts = append(parts, pattern[start:i])
				start = i + 1
			}
		}
		if closeAt >= 0 {
			break
		}
	}
	if closeAt < 0 {
		return []string{pattern}
	}
	parts = append(parts, pattern[start:closeAt])
	var out []string
	for _, part := range parts {
		out = append(out, expandBraces(pattern[:open]+part+pattern[closeAt+1:])...)
	}
	if len(out) > 64 {
		out = out[:64]
	}
	return out
}

// matchAny matches rel against every brace alternative of pattern.
func matchAny(pattern, rel string) (bool, error) {
	for _, alt := range expandBraces(pattern) {
		ok, err := pathMatch(alt, rel)
		if err != nil || ok {
			return ok, err
		}
	}
	return false, nil
}

const globScanMax = 20000

func globDir(root, pattern string) (string, error) {
	return globDirSorted(root, pattern, "path")
}

// globDirSorted collects up to globScanMax matches, sorts them ("path", or
// "modified" = newest first), and returns the first 400.
func globDirSorted(root, pattern, order string) (string, error) {
	root = expandHome(root)
	if !globOK(pattern) {
		return "", fmt.Errorf("invalid pattern")
	}
	type hit struct {
		path string
		mod  int64
	}
	var matches []hit
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
		ok, matchErr := matchAny(pat, relSlash)
		if matchErr != nil {
			return matchErr
		}
		if ok {
			var mod int64
			if order == "modified" {
				if info, err := d.Info(); err == nil {
					mod = info.ModTime().UnixNano()
				}
			}
			matches = append(matches, hit{p, mod})
			if len(matches) >= globScanMax {
				return io.EOF
			}
		}
		return nil
	})
	if err != nil && err != io.EOF {
		return "", err
	}
	sort.SliceStable(matches, func(i, j int) bool {
		if order == "modified" && matches[i].mod != matches[j].mod {
			return matches[i].mod > matches[j].mod
		}
		return matches[i].path < matches[j].path
	})
	if len(matches) > 400 {
		matches = matches[:400]
	}
	names := make([]string, len(matches))
	for i, m := range matches {
		names[i] = m.path
	}
	return strings.Join(names, "\n") + newlineIf(len(names) > 0), nil
}

func pathMatch(pattern, rel string) (bool, error) {
	pattern = strings.ReplaceAll(pattern, `\`, "/")
	rel = strings.ReplaceAll(rel, `\`, "/")
	if strings.ContainsAny(pattern, "{}") {
		return matchAny(pattern, rel)
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
	return grepDirMode(root, pattern, include, maxMatches, grepOpts{})
}

// grepOpts mirrors the rg flags the Linux path uses: -i, -C, -l, -c.
type grepOpts struct {
	ignoreCase bool
	context    int
	mode       string // "content" (default), "files", "count"
}

func grepDirMode(root, pattern, include string, maxMatches int, opt grepOpts) (string, error) {
	if pattern == "" || len(pattern) > 2000 {
		return "", fmt.Errorf("invalid pattern or path")
	}
	if opt.ignoreCase {
		pattern = "(?i)" + pattern
	}
	re, err := regexp.Compile(pattern)
	if err != nil {
		return "", fmt.Errorf("invalid pattern: %v", err)
	}
	if opt.context < 0 {
		opt.context = 0
	}
	if opt.context > 10 {
		opt.context = 10
	}
	root = expandHome(root)
	if maxMatches < 1 || maxMatches > 200 {
		maxMatches = 200
	}
	// A plain file name (no wildcards) that sits directly in root means
	// "search that file", the same rule the Linux path applies.
	if include != "" && !strings.ContainsAny(include, "*?[]{}") {
		candidate := filepath.Join(root, filepath.FromSlash(strings.ReplaceAll(include, `\`, "/")))
		if info, err := os.Stat(candidate); err == nil && info.Mode().IsRegular() {
			root, include = candidate, ""
		}
	}
	if include != "" {
		if _, err := pathMatch(include, ""); err != nil {
			return "", err
		}
	}
	rootClean := filepath.Clean(root)
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
		// Links are not followed during a walk (rg does the same), but a
		// symlink named as the search root itself is searched.
		isRoot := filepath.Clean(p) == rootClean
		if d.Type()&os.ModeSymlink != 0 && !isRoot {
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
			ok, err := matchAny(include, filepath.ToSlash(rel))
			if err != nil {
				return err
			}
			if !ok {
				return nil
			}
		}
		info, err := d.Info()
		if isRoot {
			info, err = os.Stat(p)
		}
		if err != nil || !info.Mode().IsRegular() {
			return nil
		}
		// Only an unfiltered directory walk skips large files. A file named
		// as root or selected by include is searched up to the edit ceiling
		// (a RAM guard), matching the Linux rg path.
		limit := int64(1 << 20)
		if include != "" || isRoot {
			limit = editFileMax
		}
		if info.Size() > limit {
			return nil
		}
		data, err := os.ReadFile(p)
		if err != nil || !utf8.Valid(data) {
			return nil
		}
		lines := strings.Split(string(data), "\n")
		switch opt.mode {
		case "files", "count":
			c := 0
			for _, line := range lines {
				if re.MatchString(strings.TrimSuffix(line, "\r")) {
					c++
					if opt.mode == "files" {
						break
					}
				}
			}
			if c > 0 {
				if opt.mode == "files" {
					fmt.Fprintf(&b, "%s\n", p)
				} else {
					fmt.Fprintf(&b, "%s:%d\n", p, c)
				}
				n++
			}
			if n >= maxMatches || b.Len() >= 65536 {
				return io.EOF
			}
			return nil
		}
		lastPrinted := -1
		for i, line := range lines {
			if !re.MatchString(strings.TrimSuffix(line, "\r")) {
				continue
			}
			lo, hi := i-opt.context, i+opt.context
			if lo < 0 {
				lo = 0
			}
			if hi >= len(lines) {
				hi = len(lines) - 1
			}
			if lastPrinted >= 0 && lo > lastPrinted+1 && opt.context > 0 {
				b.WriteString("--\n")
			}
			if lo <= lastPrinted {
				lo = lastPrinted + 1
			}
			for k := lo; k <= hi; k++ {
				sep := "-"
				if k == i || (k > i && re.MatchString(strings.TrimSuffix(lines[k], "\r"))) {
					sep = ":"
				}
				if k > i && sep == ":" {
					// a later match inside this window is printed by its own turn
					hi = k - 1
					break
				}
				fmt.Fprintf(&b, "%s%s%d%s%s\n", p, sep, k+1, sep, strings.TrimSuffix(lines[k], "\r"))
				lastPrinted = k
			}
			n++
			if n >= maxMatches || b.Len() >= 65536 {
				return io.EOF
			}
		}
		return nil
	})
	if walkErr != nil && walkErr != io.EOF {
		return "", walkErr
	}
	return b.String(), nil
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
