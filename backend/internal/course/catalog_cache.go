package course

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"math/rand/v2"
	"slices"
	"sync/atomic"
	"time"

	"github.com/coocood/freecache"
	"golang.org/x/sync/singleflight"
)

const (
	catalogCacheBytes  = 32 << 20
	catalogLoadTimeout = 5 * time.Second
)

type catalogCache struct {
	entries *freecache.Cache
	loads   singleflight.Group
	version atomic.Uint64
}

func newCatalogCache() *catalogCache {
	return &catalogCache{entries: freecache.NewCache(catalogCacheBytes)}
}

func (c *catalogCache) lookup(key string) ([]CourseVO, bool) {
	data, err := c.entries.Get([]byte(key))
	if err != nil {
		return nil, false
	}
	var items []CourseVO
	if json.Unmarshal(data, &items) != nil {
		return nil, false
	}
	return items, true
}

func catalogTTL(empty bool) int {
	if empty {
		return 5 + rand.IntN(5)
	}
	return 60 + rand.IntN(31)
}

func (c *catalogCache) list(ctx context.Context, keyword string, limit int,
	load func(context.Context, string, int) ([]CourseVO, error),
) ([]CourseVO, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	// 捕获版本后只读写该版本的键，避免并发写库后旧查询覆盖新结果。
	key := fmt.Sprintf("%d:%d:%x", c.version.Load(), limit, sha256.Sum256([]byte(keyword)))
	if items, ok := c.lookup(key); ok {
		return items, nil
	}
	result := c.loads.DoChan(key, func() (any, error) {
		if items, ok := c.lookup(key); ok {
			return items, nil
		}
		// 首个请求取消时，其他等待者仍可取得这次回源的结果。
		queryCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), catalogLoadTimeout)
		defer cancel()
		items, err := load(queryCtx, keyword, limit)
		if err != nil {
			return nil, err
		}
		if data, err := json.Marshal(items); err == nil {
			// 超大结果仍返回给调用方。
			_ = c.entries.Set([]byte(key), data, catalogTTL(len(items) == 0))
		}
		return items, nil
	})
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case r := <-result:
		if r.Err != nil {
			return nil, r.Err
		}
		items := r.Val.([]CourseVO)
		if r.Shared {
			return slices.Clone(items), nil
		}
		return items, nil
	}
}
