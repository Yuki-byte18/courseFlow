"""只读数据库访问。

工具层要查课程、班次、余量、课表 —— 这些都在业务库里。
**刻意直接连库而不是回调 Go 后端**：多一跳网络就多一个故障点，
而工具本来就是"读业务状态"，SQL 是最直接的表达。

写操作不在这里：写走工具层的 side_effect 通道 + 审批闸门，
仍然由 Python 执行（用户明确要求 agent 能选课退课），
但必须先过审批。
"""

from __future__ import annotations

import threading

import pymysql
from pymysql.cursors import DictCursor

from .config import get_settings

# **每个线程一个连接**，不能共享。
#
# 为什么这是致命的：工具函数通过 `asyncio.to_thread` 在线程池里执行，
# 而 pymysql 的连接**不是线程安全的**。两个线程同时在一个连接上收发，
# MySQL 协议包会串——一个线程拿走了另一个的响应，另一个就永远等下去。
# 而 pymysql 默认 read_timeout=None，也就是**永久阻塞**。
# 现象就是"卡住了，一直没动静"。
#
# 用 threading.local() 是最省事且正确的做法：线程池的线程会被复用，
# 所以连接也能被复用，不会每次新建。
_local = threading.local()

# 记录所有创建过的连接，进程退出时统一关掉
_all_conns: list[pymysql.connections.Connection] = []
_conns_lock = threading.Lock()

# 单个查询的读写超时。**必须设**：默认为 None 意味着对端不回包就永远挂着。
_READ_TIMEOUT = 20
_CONNECT_TIMEOUT = 5

# 最近执行过的 SQL（sql, args）。默认关闭，排查问题或演示时打开。
#
# 为什么需要它：Agent 说"我查到了 3 门"，你得能立刻看到它到底跑了什么 SQL、
# 绑定了什么参数。否则只能靠猜。
TRACE: list[tuple[str, tuple]] | None = None
_MAX_TRACE = 20


def start_trace() -> None:
    global TRACE
    TRACE = []


def stop_trace() -> list[tuple[str, tuple]]:
    global TRACE
    records = TRACE or []
    TRACE = None
    return records


def _record(sql: str, args: tuple | list) -> None:
    if TRACE is None:
        return
    TRACE.append((sql, tuple(args)))
    if len(TRACE) > _MAX_TRACE:
        del TRACE[0]


def _connect() -> pymysql.connections.Connection:
    settings = get_settings()
    kwargs = settings.mysql.connect_kwargs()
    kwargs["cursorclass"] = DictCursor
    # 超时必须有：pymysql 默认无限等，一旦连接卡住整个工具就永久阻塞
    kwargs["connect_timeout"] = _CONNECT_TIMEOUT
    kwargs["read_timeout"] = _READ_TIMEOUT
    kwargs["write_timeout"] = _READ_TIMEOUT
    conn = pymysql.connect(**kwargs)
    with _conns_lock:
        _all_conns.append(conn)
    return conn


def get_conn() -> pymysql.connections.Connection:
    """取当前线程的连接。断了就重连一次。

    注意：返回值**只能在本线程使用**，不要跨线程传递。
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
        return conn

    try:
        conn.ping(reconnect=False)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        conn = _connect()
        _local.conn = conn
    return conn


def _guard(sql: str) -> None:
    """拒绝多语句。

    本项目的 SQL 全部是工具实现里的常量串，只有值走 `%s` 占位符。
    真正拼进 SQL 的只有两处：`IN (%s,%s)` 的占位符个数，
    以及 ORDER BY 的方向（来自白名单）。

    这条断言是 belt-and-suspenders：万一以后有人写出 `f"... {id}"`，
    至少多语句注入会被挡住。
    """
    if ";" in sql:
        raise ValueError("SQL 中不允许出现分号（禁止多语句）")


def fetch_all(sql: str, args: tuple | list = ()) -> list[dict]:
    """执行查询，返回 dict 列表。"""
    _guard(sql)
    _record(sql, args)
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return list(cur.fetchall())


def fetch_one(sql: str, args: tuple | list = ()) -> dict | None:
    rows = fetch_all(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: tuple | list = ()) -> int:
    """执行写语句，返回影响行数。

    调用方必须已经过审批闸门 —— 这个函数本身不做任何权限判断，
    权限判断在治理层，这样职责不重叠。
    """
    _guard(sql)
    _record(sql, args)
    conn = get_conn()
    with conn.cursor() as cur:
        affected = cur.execute(sql, args)
    conn.commit()
    return affected


def close() -> None:
    """关闭本线程的连接（其他线程的在进程退出时统一关）。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def close_all() -> None:
    with _conns_lock:
        for conn in _all_conns:
            try:
                conn.close()
            except Exception:
                pass
        _all_conns.clear()
    _local.conn = None


def ping() -> tuple[bool, str]:
    """探活。启动时调用一次，把"配错了"变成一条明确的日志，而不是等用户点按钮才发现。

    返回 (ok, error)。不抛异常 —— 调用方只想记日志。
    """
    try:
        get_conn()
        row = fetch_one("SELECT 1 AS ok")
        if row and row.get("ok") == 1:
            return True, ""
        return False, "查询返回异常"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
