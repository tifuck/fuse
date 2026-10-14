package main

import (
	"bytes"
	"encoding/base64"
	"os"
	"path/filepath"
	"testing"
)

func TestReadBinary(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "image")
	data := make([]byte, 32769)
	for i := range data {
		data[i] = byte(i)
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	first, err := readBinary(path, 0, 32768)
	if err != nil {
		t.Fatal(err)
	}
	got, err := base64.StdEncoding.DecodeString(first["data"].(string))
	if err != nil || !bytes.Equal(got, data[:32768]) || first["size"] != int64(len(data)) || first["eof"] != false {
		t.Fatal(first, err)
	}
	last, err := readBinary(path, 32768, 32768)
	if err != nil || last["data"] != "AA==" || last["eof"] != true || last["identity"] != first["identity"] {
		t.Fatal(last, err)
	}
	for _, args := range [][2]int{{-1, 1}, {0, 0}, {0, binaryChunkMax + 1}, {binaryFileMax + 1, 1}} {
		if _, err := readBinary(path, args[0], args[1]); err == nil {
			t.Fatal("accepted", args)
		}
	}
	if _, err := readBinary(dir, 0, 1); err == nil {
		t.Fatal("accepted directory")
	}
	whole, err := readBinary(path, 0, binaryChunkMax)
	if err != nil || whole["eof"] != true || whole["identity"] != first["identity"] {
		t.Fatal(whole, err)
	}
	// Identity is size+mtime (no whole-file hash per chunk): a change that
	// alters the size is always seen.
	data = append(data, 'z')
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	changed, err := readBinary(path, 0, 1)
	if err != nil || changed["identity"] == first["identity"] {
		t.Fatal(changed, err)
	}
	if err := os.Truncate(path, binaryFileMax+1); err != nil {
		t.Fatal(err)
	}
	if _, err := readBinary(path, 0, 1); err == nil {
		t.Fatal("accepted oversized source")
	}
	if err := os.Truncate(path, 0); err != nil {
		t.Fatal(err)
	}
	empty, err := readBinary(path, 0, 1)
	if err != nil || empty["data"] != "" || empty["eof"] != true {
		t.Fatal(empty, err)
	}
}
