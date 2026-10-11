package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestBraceExpansionIsBoundedAcrossTheWholeRecursion(t *testing.T) {
	pattern := strings.Repeat("{a,b}", 30)
	start := time.Now()
	alts := expandBraces(pattern)
	if time.Since(start) > time.Second {
		t.Fatalf("expansion took %s", time.Since(start))
	}
	if len(alts) == 0 || len(alts) > braceMax {
		t.Fatalf("got %d alternatives", len(alts))
	}
	if got := expandBraces("*.{ts,tsx}"); len(got) != 2 || got[0] != "*.ts" || got[1] != "*.tsx" {
		t.Fatalf("simple expansion broke: %v", got)
	}
}

func TestUpdateFetchRefusesHTTPRedirect(t *testing.T) {
	plain := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"version":"99.0.0"}`))
	}))
	defer plain.Close()
	secure := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, plain.URL+"/manifest", http.StatusFound)
	}))
	defer secure.Close()
	req, _ := http.NewRequest(http.MethodGet, plain.URL, nil)
	if err := httpsOnlyRedirect(req, nil); err == nil {
		t.Fatal("http redirect target accepted")
	}
	client := secure.Client()
	client.CheckRedirect = httpsOnlyRedirect
	if _, err := client.Get(secure.URL); err == nil || !strings.Contains(err.Error(), "non-https") {
		t.Fatalf("expected non-https refusal, got %v", err)
	}
}
