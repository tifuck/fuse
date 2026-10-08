package main

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"sync"
	"unicode/utf8"
)

const (
	outCap   = 65536
	maxJobs  = 128
	maxQueue = 128
)

type jobRes struct {
	V                    int    `json:"v"`
	T                    string `json:"t"`
	ID                   string `json:"id"`
	OK                   bool   `json:"ok"`
	Exit                 int    `json:"exit"`
	Out                  string `json:"out,omitempty"`
	Err                  string `json:"err,omitempty"`
	Error                string `json:"error,omitempty"`
	Timeout              bool   `json:"timeout,omitempty"`
	Truncated            bool   `json:"truncated"` // explicit false = complete output (no omitempty)
	TerminationConfirmed bool   `json:"termination_confirmed,omitempty"`
	Stream               string `json:"stream,omitempty"`
	Data                 string `json:"data,omitempty"`
}

type liveJob struct {
	id     string
	cancel chan struct{}
}

type jobHub struct {
	mu      sync.Mutex
	live    map[string]*liveJob
	queue   chan map[string]any
	started bool
	paused  bool // update admission: refuse intake while an exit-commit is held
}

func newJobHub() *jobHub {
	h := &jobHub{
		live:  make(map[string]*liveJob),
		queue: make(chan map[string]any, maxQueue),
	}
	return h
}

func (h *jobHub) handle(s *session, msg map[string]any) {
	h.mu.Lock()
	if !h.started {
		h.started = true
		for i := 0; i < 16; i++ {
			go h.worker(s)
		}
	}
	h.mu.Unlock()
	id := fmt.Sprint(msg["id"])
	h.mu.Lock()
	if id == "" || id == "<nil>" || h.paused || len(h.live) >= maxJobs || h.live[id] != nil {
		// While update admission is held (paused) intake must be refused,
		// never queued: the holder is about to swap and exit, and a queued
		// job would be killed silently.
		h.mu.Unlock()
		// Intake runs on the socket reader: it cannot wait for reconnect or
		// queue space. No operation was accepted, so a failed reply is safe.
		s.sendRefusal(id, "agent is busy or job id is invalid")
		return
	}
	h.live[id] = &liveJob{id: id, cancel: make(chan struct{})}
	h.mu.Unlock()
	select {
	case h.queue <- msg:
	default:
		h.clear(id)
		s.sendRefusal(id, "agent is busy")
	}
}

func (h *jobHub) cancel(id string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	j := h.live[id]
	if j == nil {
		return
	}
	select {
	case <-j.cancel:
	default:
		close(j.cancel)
	}
}

func (h *jobHub) worker(s *session) {
	for msg := range h.queue {
		h.run(s, msg)
	}
}

func (h *jobHub) liveOf(id string) *liveJob {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.live[id]
}

func (h *jobHub) clear(id string) {
	h.mu.Lock()
	delete(h.live, id)
	h.mu.Unlock()
}

func jsonStr(m map[string]any, k, fallback string) string {
	v, ok := m[k]
	if !ok || v == nil {
		return fallback
	}
	switch t := v.(type) {
	case string:
		if t == "" {
			return fallback
		}
		return t
	default:
		s := fmt.Sprint(t)
		if s == "" || s == "<nil>" {
			return fallback
		}
		return s
	}
}

func jsonInt(m map[string]any, k string, fallback int) int {
	v, ok := m[k]
	if !ok || v == nil {
		return fallback
	}
	switch t := v.(type) {
	case float64:
		return int(t)
	case int:
		return t
	case string:
		n, err := strconv.Atoi(t)
		if err != nil {
			return fallback
		}
		return n
	default:
		return fallback
	}
}

func binaryInt(m map[string]any, key string, fallback, low, high int) (int, bool) {
	v, exists := m[key]
	if !exists {
		return fallback, true
	}
	switch n := v.(type) {
	case int:
		return n, n >= low && n <= high
	case float64:
		if n >= float64(low) && n <= float64(high) && n == float64(int(n)) {
			return int(n), true
		}
	}
	return 0, false
}

func jsonFlag(m map[string]any, k string) bool {
	b, _ := m[k].(bool)
	return b
}

func jsonBool(m map[string]any) bool {
	v, ok := m["replace_all"]
	if !ok {
		return false
	}
	b, _ := v.(bool)
	return b
}

func clipOut(s string) string {
	if len(s) > outCap {
		end := outCap
		for end > 0 && !utf8.RuneStart(s[end]) {
			end--
		}
		return s[:end]
	}
	return s
}

func (s *session) sendRes(id string, ok bool, exit int, out, err, errorText string) {
	// Non-exec ops (and exec failures that never started a process) leave no
	// remote process tree behind.
	s.sendResFull(id, ok, exit, out, err, errorText, false, true)
}

func (s *session) sendResFull(id string, ok bool, exit int, out, err, errorText string, timedOut bool, confirmed bool) {
	res := jobRes{V: 1, T: "res", ID: id, OK: ok, Exit: exit, Out: clipOut(out), Err: clipOut(err), Timeout: timedOut}
	// confirmed says the remote process tree is known dead. A kill without a
	// job object only reaches the shell, so its descendants may survive.
	res.TerminationConfirmed = confirmed
	res.Truncated = len(out) > outCap || len(err) > outCap
	if errorText != "" {
		res.Error = errorText
	}
	s.sendResult(res)
}

func (s *session) sendChunk(id, stream, data string) {
	if data == "" {
		return
	}
	s.sendResult(jobRes{V: 1, T: "chunk", ID: id, Stream: stream, Data: data})
}

var mutationMu sync.Mutex

func (h *jobHub) run(s *session, msg map[string]any) {
	id := jsonStr(msg, "id", "")
	op := jsonStr(msg, "op", "")
	if op == "write" || op == "replace" {
		mutationMu.Lock()
		defer mutationMu.Unlock()
	}
	job := h.liveOf(id)
	defer h.clear(id)
	if job != nil {
		select {
		case <-job.cancel:
			s.sendRes(id, false, 1, "", "", "cancelled")
			return
		default:
		}
	}
	if op == "cancel" {
		return
	}
	switch op {
	case "exec", "exec_stream":
		stream := op == "exec_stream"
		cwd := jsonStr(msg, "cwd", "~")
		cmd := jsonStr(msg, "cmd", "")
		timeout := jsonInt(msg, "timeout_ms", 30000)
		if job != nil {
			select {
			case <-job.cancel:
				s.sendRes(id, false, 1, "", "", "cancelled")
				return
			default:
			}
		}
		var cancel <-chan struct{}
		if job != nil {
			cancel = job.cancel
		}
		var onOut, onErr func([]byte)
		if stream {
			onOut = func(p []byte) { s.sendChunk(id, "out", string(p)) }
			onErr = func(p []byte) { s.sendChunk(id, "err", string(p)) }
		}
		out, errb, code, runErr, timedOut, confirmed := runExec(expandHome(cwd), cmd, timeout, cancel, nil, onOut, onErr)
		if runErr != nil {
			s.sendRes(id, false, 1, "", "", "exec failed")
			return
		}
		errText := ""
		if timedOut {
			errText = "timed out"
		} else if code != 0 {
			src := strings.TrimSpace(errb)
			if src == "" {
				src = strings.TrimSpace(out)
			}
			if i := strings.IndexByte(src, '\n'); i >= 0 {
				src = src[:i]
			}
			if len(src) > 240 {
				src = src[:240]
			}
			if src != "" {
				errText = src
			} else {
				errText = fmt.Sprintf("exit %d", code)
			}
		}
		s.sendResFull(id, code == 0, code, out, errb, errText, timedOut, confirmed)
	case "read_binary":
		offset, offsetOK := binaryInt(msg, "offset", 0, 0, binaryFileMax)
		limit, limitOK := binaryInt(msg, "limit", binaryChunkMax, 1, binaryChunkMax)
		if !offsetOK || !limitOK {
			s.sendRes(id, false, 1, "", "", "invalid binary offset or limit")
			return
		}
		result, err := readBinary(jsonStr(msg, "path", ""), offset, limit)
		if err != nil {
			s.sendRes(id, false, 1, "", err.Error(), "read_binary failed")
			return
		}
		out, err := json.Marshal(result)
		if err != nil {
			s.sendRes(id, false, 1, "", err.Error(), "read_binary failed")
			return
		}
		s.sendRes(id, true, 0, string(out), "", "")
	case "read":
		out, err := readFileSlice(jsonStr(msg, "path", ""), jsonInt(msg, "offset", 1), jsonInt(msg, "limit", 200))
		if err != nil {
			s.sendRes(id, false, 1, "", "", err.Error())
			return
		}
		s.sendRes(id, true, 0, out, "", "")
	case "write":
		if err := writeFileAtomic(jsonStr(msg, "path", ""), jsonStr(msg, "content", "")); err != nil {
			s.sendRes(id, false, 1, "", err.Error(), "write failed")
			return
		}
		s.sendRes(id, true, 0, "wrote", "", "")
	case "list":
		out, err := listTree(jsonStr(msg, "path", "."), jsonInt(msg, "depth", 1))
		if err != nil {
			s.sendRes(id, false, 1, "", err.Error(), "list failed")
			return
		}
		s.sendRes(id, true, 0, out, "", "")
	case "browse":
		js, err := browseDir(jsonStr(msg, "path", "~"))
		if err != nil {
			s.sendRes(id, false, 1, "", err.Error(), "browse failed")
			return
		}
		s.sendRes(id, true, 0, js, "", "")
	case "grep":
		path := jsonStr(msg, "path", ".")
		pattern := jsonStr(msg, "pattern", "")
		out, err := grepDirMode(path, pattern, jsonStr(msg, "include", ""), jsonInt(msg, "max_matches", 200), grepOpts{
			ignoreCase: jsonFlag(msg, "ignore_case"),
			context:    jsonInt(msg, "context", 0),
			mode:       jsonStr(msg, "mode", "content"),
		})
		if err != nil {
			msg := err.Error()
			if msg == "" {
				msg = "grep failed"
			}
			s.sendRes(id, false, 1, "", "", msg)
			return
		}
		s.sendRes(id, true, 0, out, "", "")
	case "glob":
		out, err := globDirSorted(jsonStr(msg, "path", "."), jsonStr(msg, "pattern", ""), jsonStr(msg, "sort", "path"))
		if err != nil {
			msg := err.Error()
			if msg == "" {
				msg = "glob failed"
			}
			s.sendRes(id, false, 1, "", msg, "glob failed")
			return
		}
		s.sendRes(id, true, 0, out, "", "")
	case "replace":
		var msgOut string
		var err error
		if raw, ok := msg["edits"].([]any); ok {
			edits := make([]fileEdit, 0, len(raw))
			for _, item := range raw {
				m, _ := item.(map[string]any)
				if m == nil {
					m = map[string]any{}
				}
				edits = append(edits, fileEdit{jsonStr(m, "old_string", ""), jsonStr(m, "new_string", ""), jsonBool(m)})
			}
			msgOut, err = replaceEdits(jsonStr(msg, "path", ""), edits)
		} else {
			msgOut, err = replaceFile(jsonStr(msg, "path", ""), jsonStr(msg, "old", ""), jsonStr(msg, "new", ""), jsonBool(msg))
		}
		if err != nil {
			s.sendRes(id, false, 1, "", "", err.Error())
			return
		}
		s.sendRes(id, true, 0, msgOut, "", "")
	default:
		// op=kill is intentionally gone: it read its target from "id", which is
		// the job's own id - it could only ever cancel itself. Cancellation
		// arrives as the "cancel" frame in session.go (jobs.c mirrors this).
		s.sendRes(id, false, 1, "", "", "unknown op")
	}
}
