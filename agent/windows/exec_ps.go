package main

import (
	"bytes"
	"encoding/base64"
	"unicode/utf8"
)

// streamWriter forwards output as it arrives (onOut/onErr) and/or keeps up
// to cap bytes. A UTF-8 sequence split across two pipe reads is held back
// until the next read: sent alone, each half became U+FFFD on the wire.
type streamWriter struct {
	on    func([]byte)
	buf   *bytes.Buffer
	n     int
	cap   int
	carry []byte
}

func (w *streamWriter) Write(p []byte) (int, error) {
	if w.on != nil && len(p) > 0 {
		data := append(w.carry, p...)
		tail := utf8Tail(data)
		w.carry = append([]byte(nil), data[len(data)-tail:]...)
		if len(data) > tail {
			w.on(data[:len(data)-tail])
		}
	}
	if w.buf != nil && w.n < w.cap {
		left := w.cap - w.n
		if len(p) > left {
			w.buf.Write(p[:left])
		} else {
			w.buf.Write(p)
		}
	}
	w.n += len(p)
	return len(p), nil
}

// flush sends a held-back partial sequence once the stream has ended.
func (w *streamWriter) flush() {
	if w.on != nil && len(w.carry) > 0 {
		w.on(w.carry)
		w.carry = nil
	}
}

// utf8Tail is the length of an incomplete UTF-8 sequence ending b.
func utf8Tail(b []byte) int {
	for i := len(b) - 1; i >= 0 && i >= len(b)-3; i-- {
		if utf8.RuneStart(b[i]) {
			if utf8.FullRune(b[i:]) {
				return 0
			}
			return len(b) - i
		}
	}
	return 0
}

// psGuard ends every script: powershell -Command exits 0 or 1 by the last
// statement's $?, so a failing native command (exit 2, 101, ...) read as 1.
// A failed last statement now exits with its native code when it has one.
const psGuard = "\nif (-not $?) { if ($LASTEXITCODE -is [int] -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; exit 1 }\n"

// psBootstrap is the single stdin line a (possibly pre-started) PowerShell
// runs. One line, because -Command - reads stdin as interactive input and a
// multi-line statement there needs an extra blank line to run; the script
// itself is parsed whole by ScriptBlock::Create. cwd and script travel as
// base64: PowerShell treats the curly quotes as quote characters too, so
// escaping only ' would not be enough.
func psBootstrap(cwd, cmd string) string {
	b64 := func(s string) string {
		return "([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + base64.StdEncoding.EncodeToString([]byte(s)) + "')))"
	}
	return "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; $OutputEncoding = [Console]::OutputEncoding; " +
		"$ErrorActionPreference = 'Continue'; Set-Location -LiteralPath " + b64(cwd) + "; " +
		"[Environment]::CurrentDirectory = (Get-Location -PSProvider FileSystem).ProviderPath; " +
		". ([ScriptBlock]::Create(" + b64(cmd+psGuard) + "))\n"
}
