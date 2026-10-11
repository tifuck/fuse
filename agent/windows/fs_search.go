// Filesystem tools, search: glob (with brace expansion and **) and grep.
package main

import (
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

// includeOK rejects include spellings that would let the literal-file
// narrowing below read outside the searched root: `..` segments, absolute,
// drive/UNC and home-relative paths, and ".". Mirrors the server-side guard
// in app/workspace_ops/search_cmds.py (_grep_include_arg).
func includeOK(s string) bool {
	if s == "" || s == "." {
		return false
	}
	if s[0] == '/' || s[0] == '\\' || s[0] == '~' {
		return false
	}
	if len(s) >= 2 && s[1] == ':' {
		return false
	}
	for _, part := range strings.FieldsFunc(s, func(r rune) bool { return r == '/' || r == '\\' }) {
		if part == ".." {
			return false
		}
	}
	return true
}

// matchAny matches rel against every brace alternative of pattern.
func matchAny(pattern, rel string) (bool, error) {
	alts := expandBraces(pattern)
	if len(alts) == 1 && alts[0] == pattern && strings.ContainsAny(pattern, "{}") {
		// Nothing expanded (an unmatched brace): pathMatch would hand the
		// same pattern straight back here and recurse until the stack dies.
		return false, fmt.Errorf("unmatched brace in pattern %q", pattern)
	}
	for _, alt := range alts {
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
	if include != "" && !includeOK(include) {
		return "", fmt.Errorf("include must stay inside the searched path; use a glob like *.py")
	}
	// A plain file name (no wildcards) that sits directly in root means
	// "search that file", the same rule the Linux path applies.
	if include != "" && !strings.ContainsAny(include, "*?[]{}") {
		candidate := filepath.Join(root, filepath.FromSlash(strings.ReplaceAll(include, `\`, "/")))
		if rel, err := filepath.Rel(root, candidate); err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return "", fmt.Errorf("include must stay inside the searched path; use a glob like *.py")
		}
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
