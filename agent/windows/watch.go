package main

import "sync/atomic"

// Wake callbacks fire from non-Go-origin threads (IPN change callback,
// tray window thread). Publication is atomic so those threads read a fully
// constructed closure without a data race on the package variable.
var wakeFnVal atomic.Value // stores func(int)

func setWakeFn(f func(int)) { wakeFnVal.Store(f) }

func notifyWake(reason int) {
	if f, ok := wakeFnVal.Load().(func(int)); ok && f != nil {
		f(reason)
	}
}

func startWatch(s *session) {
	setWakeFn(func(reason int) { s.requestWake(reason) })
	startWatchOS()
}
