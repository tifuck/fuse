//go:build windows

package main

import "golang.org/x/sys/windows"

// copyFileSecurity gives dst the DACL of src (protected flag included). The
// atomic write replaces a file through a temp sibling; without this the new
// file would carry the directory's inherited ACL instead of the original's,
// widening access to a file that was deliberately restricted.
func copyFileSecurity(src, dst string) error {
	sd, err := windows.GetNamedSecurityInfo(src, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		return err
	}
	dacl, _, err := sd.DACL()
	if err != nil {
		return err
	}
	ctrl, _, err := sd.Control()
	if err != nil {
		return err
	}
	info := windows.SECURITY_INFORMATION(windows.DACL_SECURITY_INFORMATION)
	if ctrl&windows.SE_DACL_PROTECTED != 0 {
		info |= windows.PROTECTED_DACL_SECURITY_INFORMATION
	} else {
		info |= windows.UNPROTECTED_DACL_SECURITY_INFORMATION
	}
	return windows.SetNamedSecurityInfo(dst, windows.SE_FILE_OBJECT, info, nil, nil, dacl, nil)
}
