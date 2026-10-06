package main

import (
	"crypto/tls"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestDialAnswersServerPing(t *testing.T) {
	got := make(chan struct{}, 1)
	reading := make(chan struct{})
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := (&websocket.Upgrader{}).Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		select {
		case <-reading:
		case <-time.After(2 * time.Second):
			return
		}
		c.SetPongHandler(func(string) error {
			select {
			case got <- struct{}{}:
			default:
			}
			return nil
		})
		if err := c.WriteControl(websocket.PingMessage, []byte("x"), time.Now().Add(time.Second)); err != nil {
			t.Errorf("ping: %v", err)
			return
		}
		// Reading is what processes control frames; a pong never surfaces
		// from NextReader itself.
		c.SetReadDeadline(time.Now().Add(2 * time.Second))
		_, _, _ = c.NextReader()
	}))
	defer srv.Close()
	saved := tlsConfig
	tlsConfig = &tls.Config{InsecureSkipVerify: true}
	defer func() { tlsConfig = saved }()
	conn, err := dialAgent(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	go func() { _, _, _ = conn.ReadMessage() }()
	close(reading)
	select {
	case <-got:
	case <-time.After(2 * time.Second):
		t.Fatal("server ping was not answered")
	}
}

func TestPingTimeout(t *testing.T) {
	var w connWatch
	connWatchInit(&w, 0, 0)
	drop, send := connWatchTick(&w, 19999, 19999, wakeNone)
	if drop != "" || send {
		t.Fatalf("pre-ping drop=%q send=%v", drop, send)
	}
	drop, send = connWatchTick(&w, pingIntervalMS, pingIntervalMS, wakeNone)
	if drop != "" || !send {
		t.Fatalf("ping due drop=%q send=%v", drop, send)
	}
	connWatchSentPing(&w, pingIntervalMS)
	drop, _ = connWatchTick(&w, pingIntervalMS+pongTimeoutMS-1, pingIntervalMS+pongTimeoutMS-1, wakeNone)
	if drop != "" {
		t.Fatalf("still in window: %s", drop)
	}
	drop, _ = connWatchTick(&w, pingIntervalMS+pongTimeoutMS, pingIntervalMS+pongTimeoutMS, wakeNone)
	if drop != "ping timeout" {
		t.Fatalf("missed pong: %q", drop)
	}
}

func TestPongResets(t *testing.T) {
	var w connWatch
	connWatchInit(&w, 0, 0)
	_, send := connWatchTick(&w, pingIntervalMS, pingIntervalMS, wakeNone)
	if !send {
		t.Fatal("expected ping")
	}
	connWatchSentPing(&w, pingIntervalMS)
	connWatchGotRx(&w, pingIntervalMS+5000)
	drop, send := connWatchTick(&w, pingIntervalMS+pongTimeoutMS+1000, pingIntervalMS+pongTimeoutMS+1000, wakeNone)
	if drop != "" || send {
		t.Fatalf("pong should prevent timeout drop=%q send=%v", drop, send)
	}
}

func TestResumeAndNetwork(t *testing.T) {
	var w connWatch
	connWatchInit(&w, 1000, 1000)
	drop, send := connWatchTick(&w, 1000, 1000, wakeResume)
	if drop != "resume" || send {
		t.Fatalf("resume: %q %v", drop, send)
	}
	connWatchInit(&w, 1000, 1000)
	drop, _ = connWatchTick(&w, 1500, 1000+5*60*1000, wakeNone)
	if drop != "resume" {
		t.Fatalf("boottime gap: %q", drop)
	}
	connWatchInit(&w, 1000, 1000)
	drop, _ = connWatchTick(&w, 2000, 2000, wakeNone)
	if drop != "" {
		t.Fatalf("false resume: %q", drop)
	}
	connWatchInit(&w, 0, 0)
	drop, _ = connWatchTick(&w, 100, 100, wakeNetwork)
	if drop != "network change" {
		t.Fatalf("network: %q", drop)
	}
}

func TestBackoff(t *testing.T) {
	want := []int{0, 1000, 2000, 5000, 10000, 15000, 30000, 30000}
	for i, w := range want {
		idx := i
		if i == 7 {
			idx = 99
		}
		if got := reconnectBackoffMS(idx, 0); got != w {
			t.Fatalf("backoff %d: got %d want %d", idx, got, w)
		}
	}
	j := reconnectBackoffMS(1, 100)
	if j < 1000 || j > 1000+250 {
		t.Fatalf("jitter %d", j)
	}
	if pingIntervalMS+pongTimeoutMS > 60000 {
		t.Fatal("ping window too long")
	}
}

func TestVersionNewer(t *testing.T) {
	cases := []struct {
		cand, cur string
		want      bool
	}{
		{"1.3.4", "1.3.3", true},
		{"1.4", "1.3.9", true},
		{"2.0.0", "1.9.9", true},
		{"1.3.3", "1.3.3", false},
		{"1.3.2", "1.3.3", false},
		{"garbage", "1.3.3", false},
		{"1.3.4", "dev", true},
	}
	for _, c := range cases {
		if got := versionNewer(c.cand, c.cur); got != c.want {
			t.Fatalf("versionNewer(%q, %q)=%v want %v", c.cand, c.cur, got, c.want)
		}
	}
}

func TestMissLimitTwo(t *testing.T) {
	var w connWatch
	connWatchInit(&w, 0, 0)
	w.missLimit = 2
	_, send := connWatchTick(&w, pingIntervalMS, pingIntervalMS, wakeNone)
	if !send {
		t.Fatal("limit2 ping")
	}
	connWatchSentPing(&w, pingIntervalMS)
	drop, send := connWatchTick(&w, pingIntervalMS+pongTimeoutMS, pingIntervalMS+pongTimeoutMS, wakeNone)
	if drop != "" {
		t.Fatalf("first miss: %q", drop)
	}
	if !send {
		t.Fatal("second ping after miss")
	}
	connWatchSentPing(&w, pingIntervalMS+pongTimeoutMS)
	drop, _ = connWatchTick(&w, pingIntervalMS+2*pongTimeoutMS, pingIntervalMS+2*pongTimeoutMS, wakeNone)
	if drop != "ping timeout" {
		t.Fatalf("second miss: %q", drop)
	}
}
