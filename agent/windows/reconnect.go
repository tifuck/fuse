package main

const (
	pingIntervalMS = 20000
	pongTimeoutMS  = 15000
	pingMissLimit  = 1
	suspendGapMS   = 2000
	backoffCapMS   = 30000
	connectTimeout = 10000
	ioTimeoutMS    = 10000
)

const (
	wakeNone = iota
	wakeResume
	wakeNetwork
)

type connWatch struct {
	lastRxMS        int64
	pingSentMS      int64
	missedPongs     int
	lastMonoMS      int64
	lastBootMS      int64
	suspendOffsetMS int64
	pingIntervalMS  int64
	pongTimeoutMS   int64
	missLimit       int
	suspendGapMS    int64
}

func connWatchInit(w *connWatch, monoMS, bootMS int64) {
	w.lastRxMS = monoMS
	w.pingSentMS = 0
	w.missedPongs = 0
	w.lastMonoMS = monoMS
	w.lastBootMS = bootMS
	w.suspendOffsetMS = bootMS - monoMS
	w.pingIntervalMS = pingIntervalMS
	w.pongTimeoutMS = pongTimeoutMS
	w.missLimit = pingMissLimit
	w.suspendGapMS = suspendGapMS
}

func connWatchGotRx(w *connWatch, monoMS int64) {
	w.lastRxMS = monoMS
	w.pingSentMS = 0
	w.missedPongs = 0
}

func connWatchSentPing(w *connWatch, monoMS int64) {
	w.pingSentMS = monoMS
}

func connWatchTick(w *connWatch, monoMS, bootMS int64, wake int) (drop string, sendPing bool) {
	if wake == wakeResume {
		return "resume", false
	}
	if wake == wakeNetwork {
		return "network change", false
	}
	offset := bootMS - monoMS
	if w.lastBootMS != 0 {
		dBoot := bootMS - w.lastBootMS
		dMono := monoMS - w.lastMonoMS
		gap := dBoot - dMono
		offDelta := offset - w.suspendOffsetMS
		if gap < 0 {
			gap = 0
		}
		if offDelta < 0 {
			offDelta = 0
		}
		if gap >= w.suspendGapMS || offDelta >= w.suspendGapMS {
			return "resume", false
		}
	}
	w.lastMonoMS = monoMS
	w.lastBootMS = bootMS
	w.suspendOffsetMS = offset

	if w.pingSentMS != 0 {
		if monoMS-w.pingSentMS >= w.pongTimeoutMS {
			w.missedPongs++
			w.pingSentMS = 0
			if w.missedPongs >= w.missLimit {
				return "ping timeout", false
			}
		}
	}
	if w.pingSentMS == 0 && monoMS-w.lastRxMS >= w.pingIntervalMS {
		sendPing = true
	}
	return "", sendPing
}

func connWatchWaitMS(w *connWatch, monoMS int64) int {
	next := w.lastRxMS + w.pingIntervalMS
	if w.pingSentMS != 0 {
		next = w.pingSentMS + w.pongTimeoutMS
	}
	left := next - monoMS
	if left < 50 {
		left = 50
	}
	if left > 1000 {
		left = 1000
	}
	return int(left)
}

var backoffMS = []int{0, 1000, 2000, 5000, 10000, 15000, backoffCapMS}

func reconnectBackoffMS(failIndex int, jitterRand uint32) int {
	if failIndex < 0 {
		failIndex = 0
	}
	if failIndex >= len(backoffMS) {
		failIndex = len(backoffMS) - 1
	}
	base := backoffMS[failIndex]
	if base <= 0 {
		return 0
	}
	span := base / 4
	return base + int(jitterRand%uint32(span+1))
}

func wakeReasonStr(wake int) string {
	switch wake {
	case wakeResume:
		return "resume"
	case wakeNetwork:
		return "network change"
	default:
		return ""
	}
}
