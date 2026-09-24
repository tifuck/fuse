//go:build windows

package main

import (
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	iphlpapi          = windows.NewLazySystemDLL("iphlpapi.dll")
	procNotifyIface   = iphlpapi.NewProc("NotifyIpInterfaceChange")
	procCancelNotify  = iphlpapi.NewProc("CancelMibChangeNotify2")
)

func startWatchOS() {
	go networkWatch()
}

func networkWatch() {
	cb := syscall.NewCallback(func(callerContext, row, notificationType uintptr) uintptr {
		notifyWake(wakeNetwork)
		return 0
	})
	var handle uintptr
	// AF_UNSPEC = 0, InitialNotification = 0
	r, _, _ := procNotifyIface.Call(0, cb, 0, 0, uintptr(unsafe.Pointer(&handle)))
	if r != 0 {
		return
	}
	select {}
}
