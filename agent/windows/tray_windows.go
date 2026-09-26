//go:build windows

package main

import (
	"os"
	"path/filepath"
	"sync"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

const (
	wmTray           = 0x0401
	wmPowerBroadcast = 0x0218
	pbtResumeAuto    = 18
	pbtResumeSuspend = 7
	nimAdd           = 0
	nimModify        = 1
	nimDelete        = 2
	nifMessage       = 1
	nifIcon          = 2
	nifTip           = 4
	imageIcon        = 1
	lrLoadFromFile   = 0x0010
	lrDefaultSize    = 0x0040
	wmRButtonUp      = 0x0205
	wmLButtonUp      = 0x0202
	wmCommand        = 0x0111
	wmDestroy        = 0x0002
	tpmRightAlign    = 0x0008
	tpmBottomAlign   = 0x0020
	tpmReturnCmd     = 0x0100
	mfString         = 0
	mfSeparator      = 0x0800
	mfGrayed         = 0x0001
	idUpdate         = 1001
	idOpen           = 1002
	idQuit           = 1003
	idQuitDisable    = 1004
)

type notifyIconData struct {
	Size            uint32
	Wnd             windows.HWND
	ID              uint32
	Flags           uint32
	CallbackMessage uint32
	Icon            windows.Handle
	Tip             [128]uint16
}

var (
	shell32           = windows.NewLazySystemDLL("shell32.dll")
	user32            = windows.NewLazySystemDLL("user32.dll")
	kernel32          = windows.NewLazySystemDLL("kernel32.dll")
	procNotify        = shell32.NewProc("Shell_NotifyIconW")
	procLoadImage     = user32.NewProc("LoadImageW")
	procCreateWin     = user32.NewProc("CreateWindowExW")
	procDefWndProc    = user32.NewProc("DefWindowProcW")
	procRegisterCls   = user32.NewProc("RegisterClassExW")
	procGetMessage    = user32.NewProc("GetMessageW")
	procTranslate     = user32.NewProc("TranslateMessage")
	procDispatch      = user32.NewProc("DispatchMessageW")
	procPostQuit      = user32.NewProc("PostQuitMessage")
	procCreatePopup   = user32.NewProc("CreatePopupMenu")
	procAppendMenu    = user32.NewProc("AppendMenuW")
	procTrackPopup    = user32.NewProc("TrackPopupMenu")
	procDestroyMenu   = user32.NewProc("DestroyMenu")
	procGetCursor     = user32.NewProc("GetCursorPos")
	procSetForeground = user32.NewProc("SetForegroundWindow")
	procDestroyWin    = user32.NewProc("DestroyWindow")
	procGetModule     = kernel32.NewProc("GetModuleHandleW")

	trayMu     sync.Mutex
	trayNID    notifyIconData
	trayReady  bool
	trayStatus = "offline"
	trayURL    string
	trayStop   chan struct{}
	trayOnQuit func(disable bool)
	trayOnUpd  func()
)

type wndClassEx struct {
	Size       uint32
	Style      uint32
	WndProc    uintptr
	ClsExtra   int32
	WndExtra   int32
	Instance   windows.Handle
	Icon       windows.Handle
	Cursor     windows.Handle
	Background windows.Handle
	MenuName   *uint16
	ClassName  *uint16
	IconSm     windows.Handle
}

type point struct{ X, Y int32 }

type msg struct {
	Hwnd    windows.HWND
	Message uint32
	WParam  uintptr
	LParam  uintptr
	Time    uint32
	Pt      point
	_       uint32
}

func loadTrayIcon() windows.Handle {
	dir := os.TempDir()
	p := filepath.Join(dir, "fuse-agent.ico")
	_ = os.WriteFile(p, iconICO, 0644)
	ptr, _ := windows.UTF16PtrFromString(p)
	h, _, _ := procLoadImage.Call(0, uintptr(unsafe.Pointer(ptr)), imageIcon, 0, 0, lrLoadFromFile|lrDefaultSize)
	return windows.Handle(h)
}

func utf16Tip(s string) [128]uint16 {
	var out [128]uint16
	u, _ := syscall.UTF16FromString(s)
	n := len(u)
	if n > 127 {
		n = 127
	}
	copy(out[:], u[:n])
	return out
}

func traySetStatus(st string) {
	trayMu.Lock()
	trayStatus = st
	if trayReady {
		trayNID.Flags = nifMessage | nifIcon | nifTip
		trayNID.Tip = utf16Tip("Fuse Connector: " + st)
		procNotify.Call(nimModify, uintptr(unsafe.Pointer(&trayNID)))
	}
	trayMu.Unlock()
}

func trayWndProc(hwnd windows.HWND, msg uint32, wParam, lParam uintptr) uintptr {
	switch msg {
	case wmPowerBroadcast:
		if wParam == pbtResumeAuto || wParam == pbtResumeSuspend {
			notifyWake(wakeResume)
		}
	case wmTray:
		if lParam == wmRButtonUp || lParam == wmLButtonUp {
			showTrayMenu(hwnd)
			return 0
		}
	case wmDestroy:
		procPostQuit.Call(0)
		return 0
	}
	r, _, _ := procDefWndProc.Call(uintptr(hwnd), uintptr(msg), wParam, lParam)
	return r
}

func showTrayMenu(hwnd windows.HWND) {
	hMenu, _, _ := procCreatePopup.Call()
	if hMenu == 0 {
		return
	}
	add := func(id uint32, flags uint32, text string) {
		p, _ := windows.UTF16PtrFromString(text)
		procAppendMenu.Call(hMenu, uintptr(flags), uintptr(id), uintptr(unsafe.Pointer(p)))
	}
	trayMu.Lock()
	st := trayStatus
	trayMu.Unlock()
	add(0, mfString|mfGrayed, "Fuse Connector · "+st)
	add(0, mfSeparator, "")
	add(idUpdate, mfString, "Update")
	add(idOpen, mfString, "Open Fuse")
	add(0, mfSeparator, "")
	add(idQuit, mfString, "Quit")
	add(idQuitDisable, mfString, "Quit and disable autostart")

	var pt point
	procGetCursor.Call(uintptr(unsafe.Pointer(&pt)))
	procSetForeground.Call(uintptr(hwnd))
	cmd, _, _ := procTrackPopup.Call(hMenu, tpmRightAlign|tpmBottomAlign|tpmReturnCmd, uintptr(pt.X), uintptr(pt.Y), 0, uintptr(hwnd), 0)
	procDestroyMenu.Call(hMenu)
	switch cmd {
	case idUpdate:
		if trayOnUpd != nil {
			go trayOnUpd()
		}
	case idOpen:
		openMachines()
	case idQuit:
		if trayOnQuit != nil {
			trayOnQuit(false)
		}
	case idQuitDisable:
		if trayOnQuit != nil {
			trayOnQuit(true)
		}
	}
}

func openMachines() {
	u := trayURL
	if u == "" {
		return
	}
	if len(u) > 0 && u[len(u)-1] == '/' {
		u = u[:len(u)-1]
	}
	u += "/hosts"
	cmd := windows.UTF16PtrFromString
	verb, _ := cmd("open")
	target, _ := cmd(u)
	shell32.NewProc("ShellExecuteW").Call(0, uintptr(unsafe.Pointer(verb)), uintptr(unsafe.Pointer(target)), 0, 0, 1)
}

func runTray(cfg *agentConfig, stop chan struct{}, onQuit func(bool), onUpdate func()) {
	trayStop = stop
	trayOnQuit = onQuit
	trayOnUpd = onUpdate
	if cfg != nil {
		trayURL = cfg.URL
	}
	className, _ := windows.UTF16PtrFromString("FuseAgentTray")
	hInst, _, _ := procGetModule.Call(0)
	icon := loadTrayIcon()
	wc := wndClassEx{
		Size:      uint32(unsafe.Sizeof(wndClassEx{})),
		WndProc:   syscall.NewCallback(trayWndProc),
		Instance:  windows.Handle(hInst),
		Icon:      icon,
		ClassName: className,
	}
	procRegisterCls.Call(uintptr(unsafe.Pointer(&wc)))
	title, _ := windows.UTF16PtrFromString("Fuse Connector")
	hwnd, _, _ := procCreateWin.Call(0, uintptr(unsafe.Pointer(className)), uintptr(unsafe.Pointer(title)), 0, 0, 0, 0, 0, 0, 0, hInst, 0)
	if hwnd == 0 {
		<-stop
		return
	}
	nid := notifyIconData{
		Wnd:             windows.HWND(hwnd),
		ID:              1,
		Flags:           nifMessage | nifIcon | nifTip,
		CallbackMessage: wmTray,
		Icon:            icon,
		Tip:             utf16Tip("Fuse Connector: " + trayStatus),
	}
	nid.Size = uint32(unsafe.Sizeof(nid))
	trayMu.Lock()
	trayNID = nid
	trayReady = true
	trayMu.Unlock()
	procNotify.Call(nimAdd, uintptr(unsafe.Pointer(&nid)))

	go func() {
		<-stop
		procNotify.Call(nimDelete, uintptr(unsafe.Pointer(&nid)))
		procDestroyWin.Call(hwnd)
	}()

	var m msg
	for {
		r, _, _ := procGetMessage.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
		if int32(r) <= 0 {
			break
		}
		procTranslate.Call(uintptr(unsafe.Pointer(&m)))
		procDispatch.Call(uintptr(unsafe.Pointer(&m)))
	}
}
