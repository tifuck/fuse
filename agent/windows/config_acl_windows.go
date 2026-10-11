//go:build windows

package main

import (
	"encoding/binary"
	"os"
	"unsafe"

	"golang.org/x/sys/windows"
)

// createSecretFile creates path exclusively with a protected DACL attached at
// creation: full control for the writing user, the previous config's owner (a
// SYSTEM rewrite must not lock the tray out), SYSTEM and BuiltinAdministrators,
// nothing inherited. Mode bits are meaningless on Windows, and hardening after
// the write leaves a window in which another local user can open the file
// under C:\ProgramData's inherited Users:(RX) ACL and keep that handle.
// previous may be "" (fresh install).
func createSecretFile(path string, previous string) (*os.File, error) {
	sids := secretSIDs(previous)
	if len(sids) == 0 {
		return nil, windows.ERROR_INVALID_SID
	}
	dacl, buf, err := buildAllowAllACL(sids)
	if err != nil {
		return nil, err
	}
	sd, err := windows.NewSecurityDescriptor()
	if err != nil {
		return nil, err
	}
	if err := sd.SetDACL(dacl, true, false); err != nil {
		return nil, err
	}
	if err := sd.SetControl(windows.SE_DACL_PROTECTED, windows.SE_DACL_PROTECTED); err != nil {
		return nil, err
	}
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return nil, err
	}
	sa := windows.SecurityAttributes{SecurityDescriptor: sd}
	sa.Length = uint32(unsafe.Sizeof(sa))
	h, err := windows.CreateFile(name, windows.GENERIC_WRITE, 0, &sa,
		windows.CREATE_NEW, windows.FILE_ATTRIBUTE_NORMAL, 0)
	_ = buf // keep the ACL bytes alive across the call
	if err != nil {
		return nil, err
	}
	return os.NewFile(uintptr(h), path), nil
}

func secretSIDs(previous string) []*windows.SID {
	sids := make([]*windows.SID, 0, 4)
	add := func(sid *windows.SID, err error) {
		if err != nil || sid == nil {
			return
		}
		owned, err := sid.Copy()
		if err != nil {
			return
		}
		for _, s := range sids {
			if s.Equals(owned) {
				return
			}
		}
		sids = append(sids, owned)
	}
	if tok, err := windows.OpenCurrentProcessToken(); err == nil {
		defer tok.Close()
		if tu, err := tok.GetTokenUser(); err == nil {
			add(tu.User.Sid, nil)
		}
	}
	if previous != "" {
		if sd, err := windows.GetNamedSecurityInfo(previous, windows.SE_FILE_OBJECT, windows.OWNER_SECURITY_INFORMATION); err == nil {
			owner, _, err := sd.Owner()
			add(owner, err)
		}
	}
	add(windows.CreateWellKnownSid(windows.WinLocalSystemSid))
	add(windows.CreateWellKnownSid(windows.WinBuiltinAdministratorsSid))
	return sids
}

// buildAllowAllACL packs one ACCESS_ALLOWED ACE (GENERIC_ALL) per SID into a
// single ACL block; SetEntriesInAcl is unexported in x/sys so the layout is
// written directly (ACE sizes are 8+sidLen, always DWORD-aligned).
func buildAllowAllACL(sids []*windows.SID) (*windows.ACL, []byte, error) {
	size := 8 // ACL header
	for _, sid := range sids {
		size += 8 + sid.Len()
	}
	buf := make([]byte, size)
	buf[0] = 2 // ACL_REVISION
	binary.LittleEndian.PutUint16(buf[2:], uint16(size))
	binary.LittleEndian.PutUint16(buf[4:], uint16(len(sids)))
	off := 8
	for _, sid := range sids {
		sidLen := sid.Len()
		buf[off] = windows.ACCESS_ALLOWED_ACE_TYPE
		binary.LittleEndian.PutUint16(buf[off+2:], uint16(8+sidLen))
		binary.LittleEndian.PutUint32(buf[off+4:], windows.GENERIC_ALL)
		copy(buf[off+8:], unsafe.Slice((*byte)(unsafe.Pointer(sid)), sidLen))
		off += 8 + sidLen
	}
	return (*windows.ACL)(unsafe.Pointer(&buf[0])), buf, nil
}
