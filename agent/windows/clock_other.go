//go:build !windows

package main

import "time"

func nowMonoMSOS() int64 { return time.Now().UnixMilli() }

func nowBootMSOS() int64 { return time.Now().UnixMilli() }
