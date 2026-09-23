package main

var wakeFn func(int)

func startWatch(s *session) {
	wakeFn = func(reason int) { s.requestWake(reason) }
	startWatchOS()
}
