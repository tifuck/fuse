//go:build windows

package main

import (
	"encoding/binary"
	"unsafe"

	"golang.org/x/sys/windows"
)

// hardenSecretFile replaces the file's DACL with explicit full-control grants
// for the writing user, the previous config's owner (a SYSTEM rewrite must not
// lock the tray out), SYSTEM and BuiltinAdministrators, and blocks inherited
// ACEs. os.WriteFile's 0600 mode is meaningless on Windows: without this the
// token-bearing config inherits C:\ProgramData's Users:(RX) ACL and any local
// user can read the device token. previous may be "" (fresh install).
func hardenSecretFile(path string, previous string) error {
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
	if len(sids) == 0 {
		return nil
	}
	dacl, buf, err := buildAllowAllACL(sids)
	if err != nil {
		return err
	}
	err = windows.SetNamedSecurityInfo(path, windows.SE_FILE_OBJECT,
		windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION,
		nil, nil, dacl, nil)
	_ = buf // keep the ACL bytes alive across the call
	return err
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
