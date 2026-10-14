package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"testing"
)

func TestReadBinaryDispatch(t *testing.T) {
	path := filepath.Join(t.TempDir(), "image")
	data := make([]byte, binaryChunkMax+1)
	for i := range data {
		data[i] = byte(i)
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	run := func(args string) jobRes {
		t.Helper()
		msg := map[string]any{}
		if err := json.Unmarshal([]byte(args), &msg); err != nil {
			t.Fatal(err)
		}
		msg["id"], msg["op"], msg["path"] = "binary", "read_binary", path
		h := newJobHub()
		h.live["binary"] = &liveJob{id: "binary", cancel: make(chan struct{})}
		s := &session{}
		h.run(s, msg)
		if len(h.live) != 0 || len(s.results) != 1 {
			t.Fatal("job not finalized")
		}
		return s.results[0]
	}
	decode := func(res jobRes) map[string]any {
		t.Helper()
		if !res.OK || res.Exit != 0 || res.Truncated || res.T != "res" || res.ID != "binary" {
			t.Fatal(res)
		}
		var payload map[string]any
		if err := json.Unmarshal([]byte(res.Out), &payload); err != nil {
			t.Fatal(err)
		}
		if len(payload) != 5 || payload["ok"] != true {
			t.Fatal(payload)
		}
		return payload
	}
	first := decode(run(`{}`))
	got, err := base64.StdEncoding.DecodeString(first["data"].(string))
	if err != nil || !bytes.Equal(got, data[:binaryChunkMax]) || first["size"] != float64(len(data)) || first["eof"] != false {
		t.Fatal("bad default chunk", err)
	}
	last := decode(run(fmt.Sprintf(`{"offset":%d,"limit":1}`, binaryChunkMax)))
	if last["identity"] != first["identity"] || last["data"] != "AA==" || last["eof"] != true {
		t.Fatal(last)
	}
	for _, args := range []string{
		`{"offset":null}`, `{"offset":"0"}`, `{"offset":false}`, `{"offset":[]}`, `{"offset":{}}`,
		`{"offset":0.5}`, `{"offset":-1}`, `{"offset":20971521}`, `{"offset":1e100}`,
		`{"limit":null}`, `{"limit":"1"}`, `{"limit":true}`, `{"limit":[]}`, `{"limit":{}}`,
		`{"limit":1.5}`, `{"limit":0}`, `{"limit":-1}`, fmt.Sprintf(`{"limit":%d}`, binaryChunkMax+1), `{"limit":1e100}`,
	} {
		res := run(args)
		if res.OK || res.Exit != 1 || res.Out != "" || res.Error != "invalid binary offset or limit" {
			t.Fatalf("accepted %s: %+v", args, res)
		}
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	res := run(`{}`)
	if res.OK || res.Out != "" || res.Error != "read_binary failed" {
		t.Fatal(res)
	}
}

func TestBinaryIntNonFinite(t *testing.T) {
	for _, value := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		if _, ok := binaryInt(map[string]any{"limit": value}, "limit", 32768, 1, 32768); ok {
			t.Fatal("accepted", value)
		}
	}
}
