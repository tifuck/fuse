package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"time"
)

type manifest struct {
	Version  string                    `json:"version"`
	Binaries map[string]manifestBinary `json:"binaries"`
}

type manifestBinary struct {
	Path   string `json:"path"`
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
	Size   int64  `json:"size"`
}

func httpGet(url string) ([]byte, error) {
	client := &http.Client{Timeout: 60 * time.Second}
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "fuse-agent/"+version)
	res, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return nil, fmt.Errorf("http %d", res.StatusCode)
	}
	return io.ReadAll(io.LimitReader(res.Body, 80<<20))
}

const updateEvery = time.Hour

// updateFetchURL resolves the manifest's binary reference to its download URL.
// Absolute URLs must already be https - the manifest's own checksum travels the
// same channel, so a plain-http fetch could be MITM'd into a payload whose hash
// the tampered manifest vouches for (the C client refuses http outright).
// Relative references join the configured base.
func updateFetchURL(base, ref string) (string, bool) {
	if ref == "" {
		return "", false
	}
	if strings.HasPrefix(ref, "https://") {
		return ref, true
	}
	// Any other absolute spelling (http://, //host, bare "http..." that the
	// old prefix heuristic would have fetched) is refused.
	if strings.Contains(ref, "://") || strings.HasPrefix(ref, "//") || strings.HasPrefix(ref, "http") {
		return "", false
	}
	if !strings.HasPrefix(base, "https://") {
		return "", false
	}
	if !strings.HasPrefix(ref, "/") {
		ref = "/" + ref
	}
	return base + ref, true
}

func parseVersion(s string) (major, minor, patch int, ok bool) {
	parts := strings.Split(strings.TrimSpace(s), ".")
	if len(parts) == 0 || len(parts) > 3 {
		return 0, 0, 0, false
	}
	nums := [3]int{}
	for i, part := range parts {
		n, err := strconv.Atoi(part)
		if err != nil || n < 0 {
			return 0, 0, 0, false
		}
		nums[i] = n
	}
	return nums[0], nums[1], nums[2], true
}

func versionNewer(candidate, current string) bool {
	ca, cb, cc, cok := parseVersion(candidate)
	if !cok {
		return false
	}
	ua, ub, uc, uok := parseVersion(current)
	if !uok {
		return true
	}
	if ca != ua {
		return ca > ua
	}
	if cb != ub {
		return cb > ub
	}
	return cc > uc
}

// updateLockStale removed: ownership is a kernel file lock on the open
// handle, so it is released only when the owning process exits (explicit
// unlock, close, crash, or os.Exit after a committed swap). It can never be
// stolen on a timer and never sticks after death.

func updateLockPath(self string) string { return self + ".update.lock" }

// Ownership for the WHOLE replacement: byte-range locks are process-owned
// (a Windows child with an inherited handle does not own the locked region;
// the locks die with the owning process), so the updater NEVER relies on a
// lock outliving itself. Instead the commit spawns a helper (self swap)
// that acquires this same lock in its own right - blocking until the
// updater releases it - and only after the helper confirms ownership via
// the readiness handshake does the updater exit. The lock is therefore
// continuously owned from updater acquire until helper exit, including
// across the delayed move/start script. The version gate is only a
// pre-filter run before the lock; it is NOT a safety mechanism.
func acquireUpdateLock(self string) (release func(), lock *os.File, ok bool) {
	f, err := os.OpenFile(updateLockPath(self), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, nil, false
	}
	if err := lockUpdateFile(f); err != nil {
		f.Close()
		return nil, nil, false
	}
	released := false
	return func() {
		if released {
			return
		}
		released = true
		unlockUpdateFile(f)
		f.Close()
	}, f, true
}

// tryBeginUpdate coalesces this process's own update callers (pump ticker,
// pre-connect check, tray menu) before they reach the cross-process lock.
var updateInFlight int32

func tryBeginUpdate() bool { return atomic.CompareAndSwapInt32(&updateInFlight, 0, 1) }

func endUpdate() { atomic.StoreInt32(&updateInFlight, 0) }

// stageUpdateBinary writes the verified payload to a fresh exclusive staging
// file next to the binary (os.CreateTemp is O_EXCL; never a fixed or
// pid-only shared name another updater could truncate under us).
func stageUpdateBinary(self string, data []byte) (string, error) {
	f, err := os.CreateTemp(filepath.Dir(self), filepath.Base(self)+".new.*")
	if err != nil {
		return "", err
	}
	path := f.Name()
	if _, err := f.Write(data); err != nil {
		f.Close()
		os.Remove(path)
		return "", err
	}
	if err := f.Close(); err != nil {
		os.Remove(path)
		return "", err
	}
	if err := os.Chmod(path, 0755); err != nil {
		os.Remove(path)
		return "", err
	}
	return path, nil
}

// fileHasSHA256 reports whether the served binary is already the one running:
// a manifest whose version outruns its binaries (a SKU left unbuilt, a
// rollback) would otherwise download and relaunch the same file every check.
func fileHasSHA256(path, want string) bool {
	f, err := os.Open(path)
	if err != nil {
		return false
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return false
	}
	return strings.EqualFold(hex.EncodeToString(h.Sum(nil)), want)
}

// maybeUpdate checks, downloads, verifies and - only when the admit callback
// grants update admission - swaps and relaunches. admit must reserve update
// admission (pause job intake, no in-flight work); holding it across
// swapAndRelaunch+exit is what makes the swap safe. A check of idle before
// the minutes-long download would be a check-then-act gap: the pump keeps
// dispatching jobs during the download. admit==nil means stage-only (manual
// CLI): the binary is verified and staged, restart stays with the operator,
// so a manual update can never kill active work or become a second daemon.
func maybeUpdate(cfg *agentConfig, force bool, admit func() (hold func(), ok bool)) {
	if cfg == nil || cfg.URL == "" {
		return
	}
	if !force && !cfg.AutoUpdate {
		return
	}
	if !tryBeginUpdate() {
		return
	}
	defer endUpdate()
	base := strings.TrimRight(cfg.URL, "/")
	if strings.HasPrefix(base, "wss://") {
		base = "https://" + strings.TrimPrefix(base, "wss://")
	}
	if !strings.HasPrefix(base, "https://") {
		log.Printf("fuse-agent: update refused; https is required")
		return
	}
	body, err := httpGet(base + "/agent/v1/manifest")
	if err != nil {
		return
	}
	var man manifest
	if json.Unmarshal(body, &man) != nil {
		return
	}
	if man.Version == "" || !versionNewer(man.Version, version) {
		return
	}
	bin, ok := man.Binaries[archSKU()]
	if !ok || bin.SHA256 == "" {
		return
	}
	ref := bin.Path
	if ref == "" && bin.URL != "" {
		ref = bin.URL
	}
	path, ok := updateFetchURL(base, ref)
	if !ok {
		log.Printf("fuse-agent: update refused; download URL is not https")
		return
	}
	self, err := os.Executable()
	if err != nil {
		return
	}
	self, _ = filepath.Abs(self)
	if fileHasSHA256(self, bin.SHA256) {
		return
	}
	data, err := httpGet(path)
	if err != nil {
		return
	}
	sum := sha256.Sum256(data)
	got := hex.EncodeToString(sum[:])
	if !strings.EqualFold(got, bin.SHA256) {
		log.Printf("fuse-agent: update checksum mismatch")
		return
	}
	release, lock, ok := acquireUpdateLock(self)
	if !ok {
		log.Printf("fuse-agent: update skipped; another updater is active")
		return
	}
	neu, err := stageUpdateBinary(self, data)
	if err != nil {
		release()
		return
	}
	if admit == nil {
		// Manual CLI update: never restarts anything itself. Moving the
		// verified payload onto the binary succeeds while the connector runs
		// on POSIX and fails with a sharing violation on a running Windows
		// exe - both are reported accurately; the operator restarts.
		if err := os.Rename(neu, self); err == nil {
			log.Printf("fuse-agent: updated to %s; restart the connector to activate", man.Version)
		} else {
			log.Printf("fuse-agent: update staged at %s; to activate: stop the connector, replace %s with it, then start the connector (rename failed: %v)", neu, self, err)
		}
		release()
		return
	}
	// Reserve admission only now, after the (minutes-long) download: the
	// holder refuses job intake and no in-flight work remains, so the swap
	// plus exit below cannot kill a running job.
	hold, ok := admit()
	if !ok {
		log.Printf("fuse-agent: update deferred; jobs are in flight")
		os.Remove(neu)
		release()
		return
	}
	log.Printf("fuse-agent: updated to %s, restarting", man.Version)
	// The helper acquires its own lock before reporting ready and keeps it
	// across the whole move/start, so ownership is continuous from here to
	// helper exit. On failure the ownership is released explicitly below.
	if err := swapAndRelaunch(self, neu, cfg.Path, lock); err != nil {
		log.Printf("fuse-agent: update restart failed: %v", err)
		os.Remove(neu)
		hold()
		release()
		return
	}
	// Commit: the process ends here. The failure-path hold()/release() must
	// NOT run - os.Exit skips them - because the helper owns the lock and
	// job admission must stay refused until this process is gone.
	os.Exit(0)
}

func doUpdate(cfg *agentConfig) int {
	// Stage-only: a manual CLI update never restarts anything, never
	// interrupts active work, and never becomes a second daemon.
	maybeUpdate(cfg, true, nil)
	return 0
}
