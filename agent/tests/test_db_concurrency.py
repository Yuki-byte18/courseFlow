"""并发查库测试。

要证明的事：多个线程同时用数据库不会互相卡住。

为什么专门测这个：pymysql 的连接不是线程安全的，而工具函数是通过
`asyncio.to_thread` 在线程池里跑的。早期实现用了一个**全局共享连接**，
两个线程同时收发时 MySQL 协议包会串——一方拿走了另一方的响应，
另一方就永远等下去。而 pymysql 默认 `read_timeout=None`，也就是永久阻塞。
用户看到的现象就是"卡住了，一分钟都没动静"。

现在每个线程一个连接 + 读写超时 20s。这个测试用来守住它。

用法：
    cd agent
    python tests/test_db_concurrency.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402

THREADS = 8
ROUNDS = 25
# 全部跑完的宽松上限。共享连接时这里会直接超时或报协议错。
TIME_LIMIT_S = 20.0

errors: list[str] = []
latencies: list[float] = []
lock = threading.Lock()


def worker(index: int) -> None:
    for _ in range(ROUNDS):
        started = time.monotonic()
        try:
            rows = db.fetch_all(
                "SELECT id, code, name FROM courses ORDER BY id LIMIT 5"
            )
            if not rows:
                raise RuntimeError("查询返回空")
            db.fetch_one("SELECT id FROM terms WHERE is_current = 1 LIMIT 1")
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"线程{index}: {exc}")
            return
        finally:
            with lock:
                latencies.append(time.monotonic() - started)


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    print("=" * 60)
    print(f"{THREADS} 个线程 × {ROUNDS} 轮查询（每轮 2 条 SQL）")

    started = time.monotonic()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=TIME_LIMIT_S + 5)

    alive = [t for t in threads if t.is_alive()]
    elapsed = time.monotonic() - started

    print("-" * 60)
    if alive:
        print(f"FAIL  {len(alive)} 个线程在 {TIME_LIMIT_S:.0f}s 后仍未结束（疑似阻塞）")
        return 1

    if errors:
        print(f"FAIL  {len(errors)} 个线程报错：")
        for item in errors[:5]:
            print(f"  - {item}")
        return 1

    total = len(latencies)
    avg = sum(latencies) / total * 1000 if total else 0
    worst = max(latencies) * 1000 if latencies else 0

    print(f"完成 {total} 轮查询，用时 {elapsed:.2f}s")
    print(f"单轮平均 {avg:.1f}ms，最慢 {worst:.1f}ms")
    print("=" * 60)

    if elapsed > TIME_LIMIT_S:
        print(f"FAIL  超过预期上限 {TIME_LIMIT_S:.0f}s，怀疑存在串行阻塞")
        return 1

    print("OK  多线程并发查库正常，无阻塞、无协议错")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
