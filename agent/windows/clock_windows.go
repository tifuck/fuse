//go:build windows

package main

import (
	"syscall"
	"unsafe"
)

var (
	modKernel32                    = syscall.NewLazyDLL("kernel32.dll")
	procQueryInterruptTime         = modKernel32.NewProc("QueryInterruptTime")
	procQueryUnbiasedInterruptTime = modKernel32.NewProc("QueryUnbiasedInterruptTime")
	procGetTickCount64             = modKernel32.NewProc("GetTickCount64")
)

func tickCount64() int64 {
	r, _, _ := procGetTickCount64.Call()
	return int64(r)
}

func nowBootMSOS() int64 {
	var t uint64
	if procQueryInterruptTime.Find() == nil {
		procQueryInterruptTime.Call(uintptr(unsafe.Pointer(&t)))
		if t != 0 {
			return int64(t / 10000)
		}
	}
	return tickCount64()
}

func nowMonoMSOS() int64 {
	var t uint64
	if procQueryUnbiasedInterruptTime.Find() == nil {
		procQueryUnbiasedInterruptTime.Call(uintptr(unsafe.Pointer(&t)))
		if t != 0 {
			return int64(t / 10000)
		}
	}
	return tickCount64()
}
