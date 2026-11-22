package course

import (
	"context"
	"errors"
	"strings"
	"sync/atomic"
	"testing"
	"testing/synctest"
	"time"
)

func TestCatalogExpiry(t *testing.T) {
	for _, tc := range []struct {
		name     string
		items    []CourseVO
		min, max time.Duration
	}{
		{"empty", []CourseVO{}, 5 * time.Second, 9 * time.Second},
		{"course", []CourseVO{{ID: 1}}, time.Minute, 90 * time.Second},
	} {
		t.Run(tc.name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				c := newCatalogCache()
				var reads int
				load := func(context.Context, string, int) ([]CourseVO, error) {
					reads++
					return tc.items, nil
				}
				check := func(wantReads int) {
					t.Helper()
					got, err := c.list(t.Context(), "math", 200, load)
					if err != nil || got == nil || len(got) != len(tc.items) || reads != wantReads {
						t.Fatalf("items=%v err=%v reads=%d, want %d", got, err, reads, wantReads)
					}
				}
				check(1)
				time.Sleep(tc.min - time.Second)
				check(1)
				time.Sleep(tc.max - tc.min + time.Second)
				check(2)
			})
		})
	}
}

func TestCatalogConcurrentMiss(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		c := newCatalogCache()
		release := make(chan struct{})
		var reads atomic.Int32
		load := func(ctx context.Context, _ string, _ int) ([]CourseVO, error) {
			reads.Add(1)
			<-release
			return []CourseVO{{ID: 1}}, ctx.Err()
		}
		first, cancel := context.WithCancel(t.Context())
		request := func(ctx context.Context) error {
			got, err := c.list(ctx, "math", 200, load)
			if err == nil && (len(got) != 1 || got[0].ID != 1) {
				return errors.New("missing course")
			}
			return err
		}
		firstDone := make(chan error, 1)
		go func() { firstDone <- request(first) }()
		synctest.Wait()
		const followers = 31
		done := make(chan error, followers)
		for range followers {
			go func() { done <- request(t.Context()) }()
		}
		synctest.Wait()
		cancel()
		if err := <-firstDone; !errors.Is(err, context.Canceled) {
			t.Fatalf("canceled caller: %v", err)
		}
		close(release)
		for range followers {
			if err := <-done; err != nil {
				t.Fatal(err)
			}
		}
		if reads.Load() != 1 {
			t.Fatalf("database reads = %d, want 1", reads.Load())
		}
	})
}

func TestCatalogInvalidationDuringLoad(t *testing.T) {
	c := newCatalogCache()
	started, release := make(chan struct{}), make(chan struct{})
	oldResult := make(chan error, 1)
	go func() {
		_, err := c.list(t.Context(), "math", 200, func(context.Context, string, int) ([]CourseVO, error) {
			close(started)
			<-release
			return []CourseVO{}, nil
		})
		oldResult <- err
	}()
	<-started
	c.version.Add(1)
	var reads int
	check := func() {
		t.Helper()
		got, err := c.list(t.Context(), "math", 200, func(context.Context, string, int) ([]CourseVO, error) {
			reads++
			return []CourseVO{{ID: 2}}, nil
		})
		if err != nil || len(got) != 1 || got[0].ID != 2 || reads != 1 {
			t.Fatalf("items=%v err=%v reads=%d", got, err, reads)
		}
	}
	check()
	close(release)
	if err := <-oldResult; err != nil {
		t.Fatal(err)
	}
	check()
}

func TestCatalogRetryAndKeyIsolation(t *testing.T) {
	c := newCatalogCache()
	failure := errors.New("database unavailable")
	var reads int
	load := func(_ context.Context, word string, limit int) ([]CourseVO, error) {
		reads++
		if reads == 1 {
			return nil, failure
		}
		return []CourseVO{{Name: word, ID: int64(limit)}}, nil
	}
	if _, err := c.list(t.Context(), "x", 1, load); !errors.Is(err, failure) {
		t.Fatal(err)
	}
	for _, key := range []struct {
		word  string
		limit int
	}{{"x", 1}, {"y", 1}, {"x", 2}, {"x", 1}} {
		got, err := c.list(t.Context(), key.word, key.limit, load)
		if err != nil || len(got) != 1 || got[0].ID != int64(key.limit) || got[0].Name != key.word {
			t.Fatalf("key=%v got=%v err=%v", key, got, err)
		}
	}
	if reads != 4 {
		t.Fatalf("database reads = %d, want 4", reads)
	}
}

func TestCatalogOversizedEntry(t *testing.T) {
	c := newCatalogCache()
	large := strings.Repeat("a", 1<<20)
	reads := 0
	load := func(context.Context, string, int) ([]CourseVO, error) {
		reads++
		return []CourseVO{{Description: large}}, nil
	}
	for range 2 {
		got, err := c.list(t.Context(), "", 200, load)
		if err != nil || len(got) != 1 || got[0].Description != large {
			t.Fatal("oversized query result lost", err)
		}
	}
	if reads != 2 {
		t.Fatalf("database reads = %d, want 2", reads)
	}
}
