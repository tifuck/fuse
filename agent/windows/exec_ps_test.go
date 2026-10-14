package main

import (
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/gorilla/websocket"
)

func TestStreamWriterKeepsSplitUTF8Whole(t *testing.T) {
	var got []string
	w := &streamWriter{on: func(p []byte) { got = append(got, string(p)) }}
	text := []byte("a€b😀c")
	for i := range text { // one byte per pipe read: every split point
		_, _ = w.Write(text[i : i+1])
	}
	w.flush()
	all := strings.Join(got, "")
	if all != string(text) {
		t.Fatalf("got %q", all)
	}
	for _, part := range got {
		if !utf8.ValidString(part) {
			t.Fatalf("chunk %q is not valid UTF-8", part)
		}
	}
	// Invalid bytes are passed on, never held forever.
	got = nil
	_, _ = w.Write([]byte{0xff, 'x'})
	if strings.Join(got, "") != "\xffx" || len(w.carry) != 0 {
		t.Fatalf("invalid byte held: %q carry=%v", got, w.carry)
	}
}

func TestPSBootstrapIsOneLineAndCarriesCwdAndScript(t *testing.T) {
	cwd := `C:\Users\Jo’s 'dir'`
	cmd := "if ($true) {\n  cargo build\n}\n"
	line := psBootstrap(cwd, cmd)
	if strings.Count(line, "\n") != 1 || !strings.HasSuffix(line, "\n") {
		t.Fatalf("bootstrap must be a single stdin line: %q", line)
	}
	enc := regexp.MustCompile(`FromBase64String\('([^']*)'\)`).FindAllStringSubmatch(line, -1)
	if len(enc) != 2 {
		t.Fatalf("want cwd and script payloads: %q", line)
	}
	dec := func(s string) string {
		b, err := base64.StdEncoding.DecodeString(s)
		if err != nil {
			t.Fatal(err)
		}
		return string(b)
	}
	if dec(enc[0][1]) != cwd {
		t.Fatalf("cwd round trip: %q", dec(enc[0][1]))
	}
	if script := dec(enc[1][1]); script != cmd+psGuard {
		t.Fatalf("script round trip: %q", script)
	}
}

// Results are delivered by one writer goroutine, in order, after being
// queued offline; producers never write to the socket themselves.
func TestWriterDeliversQueuedResultsInOrder(t *testing.T) {
	got := make(chan string, 8)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := (&websocket.Upgrader{}).Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		for {
			var res jobRes
			if err := c.ReadJSON(&res); err != nil {
				return
			}
			got <- res.ID
		}
	}))
	defer srv.Close()
	conn, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(srv.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	s := &session{}
	s.sendRes("a", true, 0, "", "", "")
	s.sendRes("b", true, 0, "", "", "")
	s.mu.Lock()
	s.conn, s.ready = conn, true
	s.mu.Unlock()
	go s.writer(conn)
	s.sendRes("c", true, 0, "", "", "")
	for _, want := range []string{"a", "b", "c"} {
		select {
		case id := <-got:
			if id != want {
				t.Fatalf("got %s, want %s", id, want)
			}
		case <-time.After(2 * time.Second):
			t.Fatalf("result %s not delivered", want)
		}
	}
	s.closeConn()
}
