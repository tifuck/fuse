package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
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
