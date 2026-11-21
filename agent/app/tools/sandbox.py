"""CodeAct 的第二道闸：子进程沙箱。

为什么必须是**独立进程**而不是同进程 exec：
  - 同进程里的 `exec` 只要有一处逃逸就能拿到整个服务的内存（包括用户的 API Key）
  - 死循环会把整个 Agent 服务拖死；独立进程可以 kill
  - 内存可以限制（POSIX 上 setrlimit；Windows 上至少能靠超时兜底）

**为什么用 subprocess 而不是 multiprocessing**（这是踩过的坑）：
Windows 上 `multiprocessing.Pipe()` 用命名管道，受限环境会直接 `WinError 5`；
spawn 模式还要重新 import 父进程的 `__main__`，在 uvicorn 里容易挂死。
subprocess + 标准输入输出是匿名管道，没有这些问题，而且 `kill()` 一定生效。

关键约束：**子进程永远不能自己执行工具**。它只能请求父进程执行，
而父进程那侧会校验"这个工具是不是只读的"。这就是 call_tool 桥接的安全边界。

诚实说明适用范围：
这不是容器级隔离。它能挡住 import、文件、网络、死循环和内存炸弹，
但挡不住恶意构造的 C 扩展利用。生产环境应该换成 gVisor / Firecracker / 容器。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# 父进程侧处理一次工具调用的回调。返回 (ok, data, error)。data 必须可 JSON 序列化。
ToolHandler = Callable[[str, dict[str, Any]], tuple[bool, Any, str]]

_RUNNER = Path(__file__).with_name("sandbox_runner.py")


@dataclass
class SandboxResult:
    ok: bool
    output: str = ""
    error: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    timed_out: bool = False


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（输出超长，已截断，原长 {len(text)} 字符）"


def run_code(
    *,
    code: str,
    on_tool: ToolHandler,
    timeout_s: float,
    max_output_chars: int,
    allowed_builtins: frozenset[str],
    memory_limit_mb: int = 256,
) -> SandboxResult:
    """在子进程里执行代码。

    on_tool 由调用方提供 —— 沙箱本身不判断工具能不能调，
    它只负责"代码跑不起来就杀掉，能跑就只把 print 输出拿回来"。
    """
    started = time.monotonic()

    if not _RUNNER.is_file():
        return SandboxResult(
            ok=False,
            error=f"沙箱执行器缺失：{_RUNNER}",
            duration_ms=0,
        )

    # -I 隔离模式：忽略 PYTHONPATH / 用户 site，避免父进程的环境泄漏进沙箱
    # -X utf8 必须加：-I 会忽略 PYTHONIOENCODING，不加的话子进程 stdio 会用
    #   系统默认编码（Windows 上是 cp936），模型代码里的中文会变成代理字符。
    cmd = [sys.executable, "-I", "-S", "-X", "utf8", str(_RUNNER)]

    # ---- Windows 上必须显式控制句柄继承 ----
    #
    # `close_fds=True`：不让子进程继承父进程的其它句柄。
    # 这条不是"优化"，是**修死锁**。uvicorn 进程里有一堆打开的 socket 和管道，
    # 子进程把它们全继承走之后，某些句柄的引用计数和 I/O 完成状态会互相牵制，
    # 表现就是父进程在等子进程、子进程在等一个永远不会来的读事件 ——
    # 日志上完全静默，连事件循环都停住（看门狗也跑不了）。
    #
    # `CREATE_NO_WINDOW`：不额外弹控制台，避免在某些宿主里抢到前台句柄。
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "bufsize": 1,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603
    except OSError as exc:
        # 起不来就**立刻**报错。绝不能让它变成"卡住"。
        return SandboxResult(
            ok=False,
            error=f"无法启动沙箱进程：{exc}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    assert proc.stdin is not None and proc.stdout is not None

    # 读线程 + 队列：subprocess 的 readline 是阻塞的，必须放到线程里，
    # 主线程用带超时的 get 才能实现"超时就 kill"。
    lines: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                lines.put(line)
        except Exception:  # noqa: BLE001
            pass
        finally:
            lines.put(None)

    threading.Thread(target=reader, daemon=True).start()

    def send(payload: dict[str, Any]) -> None:
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")  # type: ignore[union-attr]
        proc.stdin.flush()  # type: ignore[union-attr]

    deadline = time.monotonic() + timeout_s
    tool_calls: list[dict[str, Any]] = []
    output = ""
    error = ""
    ok = False
    timed_out = False

    try:
        send(
            {
                "op": "run",
                "code": code,
                "builtins": sorted(allowed_builtins),
                "memory_limit_mb": memory_limit_mb,
            }
        )

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break

            try:
                line = lines.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if proc.poll() is not None:
                    # 进程没了但没发 done —— 崩了
                    break
                continue

            if line is None:
                break

            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue

            op = message.get("op")

            if op == "call":
                tool_calls.append(
                    {"name": message.get("name"), "args": message.get("args") or {}}
                )
                try:
                    tool_ok, data, tool_error = on_tool(
                        str(message.get("name")), dict(message.get("args") or {})
                    )
                except Exception as exc:  # noqa: BLE001
                    tool_ok, data, tool_error = False, None, f"宿主执行工具失败：{exc}"
                try:
                    send(
                        {
                            "op": "result",
                            "id": message.get("id"),
                            "ok": tool_ok,
                            "data": data,
                            "error": tool_error,
                        }
                    )
                except OSError:
                    break

            elif op == "done":
                ok = bool(message.get("ok"))
                output = str(message.get("stdout") or "")
                error = str(message.get("error") or "")
                break
    except OSError as exc:
        # 管道断了：子进程提前退出
        error = error or f"沙箱通信中断：{exc}"
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    duration_ms = int((time.monotonic() - started) * 1000)

    if timed_out:
        return SandboxResult(
            ok=False,
            output=_truncate(output, max_output_chars),
            error=f"代码执行超时（>{timeout_s:.0f}s），已终止",
            tool_calls=tool_calls,
            duration_ms=duration_ms,
            timed_out=True,
        )

    if not ok and not error:
        error = "代码执行失败（沙箱进程异常退出）"

    return SandboxResult(
        ok=ok,
        output=_truncate(output, max_output_chars),
        error=error,
        tool_calls=tool_calls,
        duration_ms=duration_ms,
    )
