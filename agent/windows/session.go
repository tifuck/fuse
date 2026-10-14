package main

import (
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net/http"
	"net/url"
	"os"
	"os/user"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

const wsMax = 1024 * 1024

// processInst names this process to the server: jobs parked by a session of
// another process can never be answered, so the server fails them at once.
var processInst = fmt.Sprintf("%016x", rand.Uint64())

// Overridable so tests can trust httptest's certificate. Production keeps
// the default verification.
var tlsConfig = &tls.Config{MinVersion: tls.VersionTLS12}

// activeSession exposes the in-process session to other goroutines (tray
// update admission). Atomic publication: the tray menu goroutine must read a
// fully constructed session. nil in CLI/headless processes.
var activeSession atomic.Pointer[session]

type session struct {
	cfg           *agentConfig
	mu            sync.Mutex
	writeMu       sync.Mutex // one websocket writer at a time (gorilla)
	conn          *websocket.Conn
	stop          chan struct{}
	wake          chan int
	jobs          *jobHub
	status        string
	ready         bool
	results       []jobRes
	resultSeq     uint64
	resultSpace   *sync.Cond
	resultReady   *sync.Cond
	droppedChunks map[string]bool
	nextUpdate    time.Time
}

// Chunks leave this many result slots free for terminal results.
const streamReserve = 16

// reserveUpdateAdmission pauses job intake and verifies nothing is in flight.
// The hold must span swapAndRelaunch plus process exit: intake refusal makes
// the invariant durable for the minutes between the reservation and the
// exit, instead of a one-off idle check racing the pump.
func (s *session) reserveUpdateAdmission() (hold func(), ok bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.results) != 0 {
		return nil, false
	}
	s.jobs.mu.Lock()
	defer s.jobs.mu.Unlock()
	if len(s.jobs.live) != 0 {
		return nil, false
	}
	s.jobs.paused = true
	return func() {
		s.jobs.mu.Lock()
		s.jobs.paused = false
		s.jobs.mu.Unlock()
	}, true
}

func archSKU() string {
	if runtime.GOARCH == "arm64" {
		return "windows-arm64"
	}
	return "windows-amd64"
}

func hostnameUser() (string, string) {
	host, _ := os.Hostname()
	if host == "" {
		host = "windows"
	}
	u := os.Getenv("USERNAME")
	if u == "" {
		if cu, err := user.Current(); err == nil {
			u = cu.Username
		}
	}
	if i := strings.LastIndexAny(u, `/\`); i >= 0 {
		u = u[i+1:]
	}
	if u == "" {
		u = "user"
	}
	return host, u
}

func connectURL(base string) (string, error) {
	u, err := url.Parse(strings.TrimRight(base, "/"))
	if err != nil {
		return "", err
	}
	if u.Scheme != "https" && u.Scheme != "wss" {
		return "", fmt.Errorf("https required")
	}
	u.Scheme = "wss"
	// Keep a path prefix (Fuse served under one), as the update fetch does.
	u.Path = strings.TrimRight(u.Path, "/") + "/agent/v1/connect"
	u.RawQuery = ""
	u.Fragment = ""
	return u.String(), nil
}

func dialAgent(raw string) (*websocket.Conn, error) {
	ws, err := connectURL(raw)
	if err != nil {
		return nil, err
	}
	d := websocket.Dialer{
		HandshakeTimeout: connectTimeout * time.Millisecond,
		TLSClientConfig:  tlsConfig,
	}
	hdr := http.Header{}
	hdr.Set("User-Agent", "fuse-agent/"+version)
	conn, _, err := d.Dial(ws, hdr)
	if err != nil {
		return nil, err
	}
	conn.SetReadLimit(wsMax)
	// Default handler answers pings from the reader, so a full inbound queue
	// stalls the pong past the server's ping budget and the socket is closed.
	conn.SetPingHandler(func(data string) error {
		return conn.WriteControl(websocket.PongMessage, []byte(data), time.Now().Add(ioTimeoutMS*time.Millisecond))
	})
	conn.SetPongHandler(func(string) error { return nil })
	return conn, nil
}

func (s *session) write(conn *websocket.Conn, v any) error {
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	conn.SetWriteDeadline(time.Now().Add(ioTimeoutMS * time.Millisecond))
	return conn.WriteJSON(v)
}

func (s *session) sendJSON(v any) error {
	s.mu.Lock()
	c := s.conn
	s.mu.Unlock()
	if c == nil {
		return io.ErrClosedPipe
	}
	return s.write(c, v)
}

// condsLocked creates the queue conditions on first use (s.mu held).
func (s *session) condsLocked() {
	if s.resultSpace == nil {
		s.resultSpace = sync.NewCond(&s.mu)
		s.resultReady = sync.NewCond(&s.mu)
	}
}

func (s *session) enqueueLocked(res jobRes) {
	s.resultSeq++
	res.seq = s.resultSeq
	s.results = append(s.results, res)
	s.resultReady.Signal()
}

// Job results survive a connection flap and must never precede hello_ok.
// The hub's in-flight limit bounds this queue during disconnection. Only
// the connection's writer goroutine touches the network.
func (s *session) sendResult(res jobRes) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.condsLocked()
	for len(s.results) >= maxJobs {
		s.resultSpace.Wait()
	}
	if res.T == "res" && s.droppedChunks[res.ID] {
		res.Truncated = true
		delete(s.droppedChunks, res.ID)
	}
	s.enqueueLocked(res)
}

// sendChunk waits for room like the C connector (the command blocks on its
// pipe meanwhile), leaving streamReserve slots for terminal results. It
// gives up - flagging the job's result truncated - once stop closes or
// until passes, so a dead link cannot hold the job past its deadline.
func (s *session) sendChunk(id, stream, data string, stop <-chan struct{}, until time.Time) {
	if data == "" {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.condsLocked()
	// ponytail: polls every 10 ms only while the queue is full (slow or lost
	// link); a cond with a deadline would avoid it.
	for len(s.results) >= maxJobs-streamReserve {
		if isStopped(stop) || time.Now().After(until) {
			if s.droppedChunks == nil {
				s.droppedChunks = make(map[string]bool)
			}
			s.droppedChunks[id] = true
			return
		}
		s.mu.Unlock()
		time.Sleep(10 * time.Millisecond)
		s.mu.Lock()
	}
	s.enqueueLocked(jobRes{V: 1, T: "chunk", ID: id, Stream: stream, Data: data})
}

// Refusals run on the reader goroutine, which must never wait for queue
// space that only a reconnect on that same goroutine can make available.
func (s *session) sendRefusal(id, reason string) {
	res := jobRes{V: 1, T: "res", ID: id, Exit: 1, Error: reason, TerminationConfirmed: true}
	s.mu.Lock()
	s.condsLocked()
	if len(s.results) < maxJobs {
		s.enqueueLocked(res)
		s.mu.Unlock()
		return
	}
	c := s.conn
	ready := s.ready
	s.mu.Unlock()
	if ready && c != nil {
		_ = s.write(c, res)
	}
}

// drainResults sends queued results in order, popping each only after its
// send succeeded. Called with s.mu held; send runs without it, so producers
// and pings never wait on the network. A result popped meanwhile (the link
// was replaced) is not popped twice.
func (s *session) drainResults(send func(jobRes) error) error {
	for len(s.results) > 0 {
		res := s.results[0]
		s.mu.Unlock()
		err := send(res)
		s.mu.Lock()
		if err != nil {
			return err
		}
		if len(s.results) > 0 && s.results[0].seq == res.seq {
			s.results[0] = jobRes{}
			s.results = s.results[1:]
		}
		if s.resultSpace != nil {
			s.resultSpace.Broadcast()
		}
	}
	return nil
}

// writer delivers queued results on conn until it is replaced or closed. A
// failed write closes conn, so the reader errors and the session reconnects
// at once instead of waiting for a ping timeout.
func (s *session) writer(conn *websocket.Conn) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.condsLocked()
	for s.conn == conn {
		if len(s.results) == 0 {
			s.resultReady.Wait()
			continue
		}
		if err := s.drainResults(func(res jobRes) error { return s.write(conn, res) }); err != nil {
			s.mu.Unlock()
			_ = conn.Close()
			s.mu.Lock()
			return
		}
	}
}

func (s *session) idleForUpdate() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.results) != 0 {
		return false
	}
	s.jobs.mu.Lock()
	defer s.jobs.mu.Unlock()
	return len(s.jobs.live) == 0
}

func (s *session) setStatus(st string) {
	s.mu.Lock()
	s.status = st
	s.mu.Unlock()
	traySetStatus(st)
}

func (s *session) Status() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.status == "" {
		return "offline"
	}
	return s.status
}

func (s *session) closeConn() {
	s.mu.Lock()
	c := s.conn
	s.conn = nil
	s.ready = false
	if s.resultReady != nil {
		s.resultReady.Broadcast() // the writer exits
	}
	s.mu.Unlock()
	if c != nil {
		_ = c.Close()
	}
}

func (s *session) requestWake(reason int) {
	select {
	case s.wake <- reason:
	default:
	}
	s.closeConn()
}

func pairOnce(cfg *agentConfig) error {
	conn, err := dialAgent(cfg.URL)
	if err != nil {
		return fmt.Errorf("tls connect failed (need outbound HTTPS to %s): %w", cfg.URL, err)
	}
	defer conn.Close()
	host, userName := hostnameUser()
	hello := map[string]any{
		"v": 1, "t": "hello",
		"hostname": host, "user": userName,
		"os": "windows", "arch": archSKU(), "ver": version, "inst": processInst,
	}
	if cfg.Code != "" {
		hello["code"] = cfg.Code
	}
	if cfg.Token != "" {
		hello["token"] = cfg.Token
	}
	conn.SetWriteDeadline(time.Now().Add(ioTimeoutMS * time.Millisecond))
	if err := conn.WriteJSON(hello); err != nil {
		return fmt.Errorf("hello failed: %w", err)
	}
	conn.SetReadDeadline(time.Now().Add(15 * time.Second))
	_, data, err := conn.ReadMessage()
	if err != nil {
		return fmt.Errorf("no hello reply: %w", err)
	}
	var reply map[string]any
	if err := json.Unmarshal(data, &reply); err != nil {
		return fmt.Errorf("pairing failed")
	}
	if fmt.Sprint(reply["t"]) != "hello_ok" {
		errText := fmt.Sprint(reply["error"])
		if errText == "" || errText == "<nil>" {
			errText = "pairing failed"
		}
		return fmt.Errorf("%s", errText)
	}
	if tok, _ := reply["token"].(string); tok != "" {
		cfg.Token = tok
		cfg.Code = ""
	}
	alias, _ := reply["alias"].(string)
	if alias == "" {
		alias = "machine"
	}
	log.Printf("fuse-agent: paired as %s", alias)
	return nil
}

func runSession(cfg *agentConfig, stop chan struct{}) {
	s := &session{
		cfg:  cfg,
		stop: stop,
		wake: make(chan int, 8),
		jobs: newJobHub(),
	}
	activeSession.Store(s)
	startWatch(s)
	host, userName := hostnameUser()
	failIndex := 0
	authBackoff := 0
	ever := false
	attempts := 0
	s.setStatus("offline")
	for {
		select {
		case <-stop:
			s.setStatus("offline")
			return
		default:
		}
		attempts++
		wake := 0
		select {
		case wake = <-s.wake:
		default:
		}
		// No update fetch before connecting (an extra HTTPS round trip on
		// every reconnect): the pump checks soon after hello_ok instead.
		if wake == wakeResume {
			log.Printf("fuse-agent: resume detected")
		} else if wake == wakeNetwork {
			log.Printf("fuse-agent: network up")
		}
		if ever || attempts > 1 {
			log.Printf("fuse-agent: reconnect attempt %d", attempts)
		}
		s.setStatus("reconnecting")
		conn, err := dialAgent(cfg.URL)
		if err != nil {
			if failIndex < 6 {
				failIndex++
			}
			wait := reconnectBackoffMS(failIndex, rand.Uint32())
			log.Printf("fuse-agent: connect failed; retry in %dms", wait)
			if sleepStop(stop, s.wake, time.Duration(wait)*time.Millisecond) {
				return
			}
			continue
		}
		hello := map[string]any{
			"v": 1, "t": "hello",
			"hostname": host, "user": userName,
			"os": "windows", "arch": archSKU(), "ver": version, "inst": processInst,
		}
		if cfg.Code != "" {
			hello["code"] = cfg.Code
		}
		if cfg.Token != "" {
			hello["token"] = cfg.Token
		}
		s.mu.Lock()
		s.conn = conn
		s.ready = false
		s.mu.Unlock()
		if err := s.sendJSON(hello); err != nil {
			s.closeConn()
			if failIndex < 6 {
				failIndex++
			}
			wait := reconnectBackoffMS(failIndex, rand.Uint32())
			log.Printf("fuse-agent: disconnected (hello failed); reconnecting")
			if sleepStop(stop, s.wake, time.Duration(wait)*time.Millisecond) {
				return
			}
			continue
		}
		conn.SetReadDeadline(time.Now().Add(15 * time.Second))
		_, data, err := conn.ReadMessage()
		if err != nil {
			s.closeConn()
			if failIndex < 6 {
				failIndex++
			}
			wait := reconnectBackoffMS(failIndex, rand.Uint32())
			log.Printf("fuse-agent: disconnected (no hello reply); reconnecting")
			if sleepStop(stop, s.wake, time.Duration(wait)*time.Millisecond) {
				return
			}
			continue
		}
		var reply map[string]any
		_ = json.Unmarshal(data, &reply)
		if fmt.Sprint(reply["t"]) != "hello_ok" {
			errText := fmt.Sprint(reply["error"])
			log.Printf("fuse-agent: %s", errText)
			s.closeConn()
			// A server that sends "code" says auth outright; older ones are
			// classified by their wording.
			code, _ := reply["code"].(string)
			auth := code == "auth"
			if code == "" {
				auth = strings.Contains(errText, "invalid") || strings.Contains(errText, "expired") || strings.Contains(errText, "token")
			}
			if auth {
				if authBackoff < 60 {
					authBackoff = 60
				} else {
					authBackoff *= 2
					if authBackoff > 600 {
						authBackoff = 600
					}
				}
				if sleepStop(stop, s.wake, time.Duration(authBackoff)*time.Second) {
					return
				}
				continue
			}
			if failIndex < 6 {
				failIndex++
			}
			wait := reconnectBackoffMS(failIndex, rand.Uint32())
			if sleepStop(stop, s.wake, time.Duration(wait)*time.Millisecond) {
				return
			}
			continue
		}
		if tok, _ := reply["token"].(string); tok != "" {
			cfg.Token = tok
			cfg.Code = ""
			if err := saveConfig(cfg); err != nil {
				log.Printf("fuse-agent: could not save the device token to %s: %v; this connector will need a new install code after it restarts", cfg.Path, err)
			}
		}
		alias, _ := reply["alias"].(string)
		if alias == "" {
			alias = "machine"
		}
		log.Printf("fuse-agent: connected as %s", alias)
		authBackoff = 0
		attempts = 0
		ever = true
		s.setStatus("online")
		s.mu.Lock()
		s.ready = true
		// A server restart drops every session: check for an update soon.
		if soon := time.Now().Add(time.Minute); s.nextUpdate.IsZero() || s.nextUpdate.After(soon) {
			s.nextUpdate = soon
		}
		s.mu.Unlock()
		go s.writer(conn)
		sessionStart := nowMonoMS()
		replaced, drop, gotFrame := s.pump(conn, stop, cfg)
		s.closeConn()
		s.setStatus("offline")
		if isStopped(stop) {
			return
		}
		if replaced {
			wait := 120 + rand.Intn(61)
			log.Printf("fuse-agent: replaced by another instance; retrying in %ds", wait)
			if sleepStop(stop, s.wake, time.Duration(wait)*time.Second) {
				return
			}
			continue
		}
		if drop == "resume" {
			log.Printf("fuse-agent: resume detected")
		} else if drop == "network change" {
			log.Printf("fuse-agent: network up")
		}
		failIndex = failIndexAfterSession(failIndex, nowMonoMS()-sessionStart, gotFrame)
		wait := reconnectBackoffMS(failIndex, rand.Uint32())
		log.Printf("fuse-agent: disconnected (%s); reconnecting in %dms", emptyAs(drop, "socket closed"), wait)
		if sleepStop(stop, s.wake, time.Duration(wait)*time.Millisecond) {
			return
		}
		if failIndex == 0 {
			attempts = 0
		}
	}
}

func emptyAs(s, fallback string) string {
	if s == "" {
		return fallback
	}
	return s
}

func (s *session) pump(conn *websocket.Conn, stop chan struct{}, cfg *agentConfig) (replaced bool, drop string, gotFrame bool) {
	var watch connWatch
	connWatchInit(&watch, nowMonoMS(), nowBootMS())
	connWatchGotRx(&watch, nowMonoMS())
	conn.SetReadDeadline(time.Time{})
	incoming := make(chan []byte, 8)
	readErr := make(chan error, 1)
	done := make(chan struct{})
	defer close(done)
	go func() {
		for {
			_, data, err := conn.ReadMessage()
			if err != nil {
				readErr <- err
				return
			}
			select {
			case incoming <- data:
			case <-done:
				return
			}
		}
	}()
	// Fires at the next ping/pong deadline (at most 1 s): a fixed 50 ms
	// ticker woke an idle connector 20 times a second.
	timer := time.NewTimer(time.Duration(connWatchWaitMS(&watch, nowMonoMS())) * time.Millisecond)
	defer timer.Stop()
	for {
		select {
		case <-stop:
			return false, "stop", gotFrame
		case wake := <-s.wake:
			d, _ := connWatchTick(&watch, nowMonoMS(), nowBootMS(), wake)
			if d != "" {
				return false, d, gotFrame
			}
		case err := <-readErr:
			if err != nil {
				return false, "socket closed", gotFrame
			}
		case data := <-incoming:
			connWatchGotRx(&watch, nowMonoMS())
			gotFrame = true
			var msg map[string]any
			if json.Unmarshal(data, &msg) != nil {
				continue
			}
			switch fmt.Sprint(msg["t"]) {
			case "job":
				s.jobs.handle(s, msg)
			case "cancel":
				s.jobs.cancel(fmt.Sprint(msg["id"]))
			case "bye":
				if fmt.Sprint(msg["reason"]) == "replaced" {
					return true, "replaced", gotFrame
				}
				return false, "server bye", gotFrame
			case "ping":
				_ = s.sendJSON(map[string]any{"v": 1, "t": "pong"})
			case "pong":
			}
		case <-timer.C:
			d, send := connWatchTick(&watch, nowMonoMS(), nowBootMS(), wakeNone)
			if d != "" {
				return false, d, gotFrame
			}
			s.mu.Lock()
			due := !time.Now().Before(s.nextUpdate)
			s.mu.Unlock()
			if due && s.idleForUpdate() {
				s.mu.Lock()
				s.nextUpdate = time.Now().Add(updateEvery)
				s.mu.Unlock()
				// Off the pump: jobs, cancels and pings keep flowing while the
				// manifest and binary download.
				go func() {
					err := maybeUpdate(cfg, false, func() (func(), bool) {
						// Frames waiting unread in incoming would be dropped
						// by the exit.
						if len(incoming) != 0 {
							return nil, false
						}
						return s.reserveUpdateAdmission()
					})
					if errors.Is(err, errUpdateDeferred) {
						s.mu.Lock()
						s.nextUpdate = time.Now().Add(time.Minute)
						s.mu.Unlock()
					}
					logUpdate(err)
				}()
			}
			if send {
				if err := s.sendJSON(map[string]any{"v": 1, "t": "ping"}); err != nil {
					return false, "io error", gotFrame
				}
				connWatchSentPing(&watch, nowMonoMS())
			}
			timer.Reset(time.Duration(connWatchWaitMS(&watch, nowMonoMS())) * time.Millisecond)
		}
	}
}

func sleepStop(stop chan struct{}, wake chan int, d time.Duration) bool {
	if d <= 0 {
		select {
		case <-stop:
			return true
		default:
			return false
		}
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-stop:
		return true
	case <-wake:
		return false
	case <-t.C:
		return false
	}
}

func isStopped(stop <-chan struct{}) bool {
	select {
	case <-stop:
		return true
	default:
		return false
	}
}
