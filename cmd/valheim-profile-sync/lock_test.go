package main

import (
	"testing"
	"time"
)

func TestProfileLockSerializesConcurrentSynchronization(t *testing.T) {
	root := t.TempDir()
	first, err := acquireProfileLock(root)
	if err != nil {
		t.Fatal(err)
	}
	acquired := make(chan *profileLock, 1)
	failed := make(chan error, 1)
	entered := make(chan struct{})
	go func() {
		close(entered)
		second, err := acquireProfileLock(root)
		if err != nil {
			failed <- err
			return
		}
		acquired <- second
	}()
	<-entered
	select {
	case second := <-acquired:
		second.Close()
		t.Fatal("second synchronization acquired the profile lock before release")
	case err := <-failed:
		t.Fatal(err)
	default:
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	select {
	case second := <-acquired:
		if err := second.Close(); err != nil {
			t.Fatal(err)
		}
	case err := <-failed:
		t.Fatal(err)
	case <-time.After(time.Second):
		t.Fatal("second synchronization did not acquire the released profile lock")
	}
}
