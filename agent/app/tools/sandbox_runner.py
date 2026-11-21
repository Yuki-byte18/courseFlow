"""CodeAct 沙箱子进程（独立脚本，**不 import 项目任何模块**）。

为什么是独立脚本而不是 multiprocessing：
在 Windows 上 `multiprocessing.Pipe()` 用的是**命名管道**，而命名管道在
不少受限环境里会被拒绝（`WinError 5`）。更糟的是 spawn 模式要重新 import
父进程的 `__main__`，在 uvicorn 这种"入口是框架"的进程里很容易出怪问题。
表现就是"沙箱起不来"或者"父进程永远等不到结果"。

换成 subprocess + 标准输入输出（匿名管道）之后：
  - 不 pickle 任何东西
  - 不重新 import 父进程
  - `proc.kill()` 一定生效
  - 失败立刻返回错误，而不是挂住

协议（每行一个 JSON）：
    父 → 子  {"op":"run","code":"...","builtins":["print","len",...]}
    子 → 父  {"op":"call","id":1,"name":"search_offerings","args":{...}}
    父 → 子  {"op":"result","id":1,"ok":true,"data":...,"error":""}
    子 → 父  {"op":"done","ok":true,"stdout":"...","error":""}

注意：用户代码的 `print()` 被重定向到内存缓冲，
**不会污染协议通道**。协议写的是 `real_out`（重定向前抓到的 stdout）。
"""

from __future__ import annotations

import builtins as _builtins
import contextlib
import io
import json
import os
import sys
import traceback


def _force_utf8_stdio() -> None:
    """把标准输入输出强制成 UTF-8。

    为什么必须有这一步：父进程用 `-I`（隔离模式）启动子进程，而 `-I` 会
    **忽略 PYTHONIOENCODING**。于是子进程的 stdin 退回系统默认编码（Windows
    上是 cp936），读到中文就变成代理字符（`\\udcb1` 这种），
    紧接着 `compile()` 抛 UnicodeEncodeError —— 模型写的代码里只要有中文字符串就必炸。

    父进程也已经加了 `-X utf8`，这里是第二道保险：不依赖任何环境变量。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="strict")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def _limit_memory(megabytes: int) -> None:
    """限制子进程可用的地址空间。

    为什么有用：模型完全可能写一个 `[0] * 10**9` 把内存吃光。
    超时只能拦住"算不完"，拦不住"瞬间吃满内存把机器拖死" —— 后者是
    分配成功、立刻 OOM，父进程还来不及反应。所以要在执行前就把上限压死。

    实现差异（必须诚实说明）：
      - POSIX：`resource.setrlimit(RLIMIT_AS)` 是硬限制，超了直接 MemoryError。
      - Windows：**标准库没有等价的资源限制**，只能靠超时兜底。
        真正的内存封顶需要 Job Object（ctypes 调 Win32），那是容器级方案的活。
        这里选择不假装做到了：Windows 上跳过，并在日志里说清楚。
    """
    if megabytes <= 0:
        return
    if os.name == "nt":
        print(f"[sandbox] Windows 无法设置内存上限（期望 {megabytes}MB），仅靠超时兜底", file=sys.stderr)
        return
    try:
        import resource

        limit = megabytes * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    _force_utf8_stdio()

    real_out = sys.stdout
    real_in = sys.stdin

    def send(payload: dict) -> None:
        real_out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        real_out.flush()

    first = real_in.readline()
    if not first:
        return 0
    try:
        init = json.loads(first)
    except json.JSONDecodeError:
        return 1

    code = str(init.get("code") or "")
    names = list(init.get("builtins") or [])

    # 在执行用户代码**之前**把内存上限压死（POSIX 有效，Windows 见函数注释）
    _limit_memory(int(init.get("memory_limit_mb") or 0))

    seq = 0

    def call_tool(name: str, args: dict | None = None):
        """沙箱内唯一的对外通道。请求父进程执行只读工具。"""
        nonlocal seq
        seq += 1
        send({"op": "call", "id": seq, "name": str(name), "args": dict(args or {})})
        line = real_in.readline()
        if not line:
            raise RuntimeError("父进程已断开")
        message = json.loads(line)
        if not message.get("ok"):
            return {"error": message.get("error") or "工具执行失败"}
        return message.get("data")

    allowed: dict[str, object] = {}
    for name in names:
        if name in ("True", "False", "None"):
            continue
        if hasattr(_builtins, name):
            allowed[name] = getattr(_builtins, name)
    # print 一定要有：输出是唯一的结果回传方式
    allowed["print"] = print

    globals_dict: dict[str, object] = {
        "__builtins__": allowed,
        "__name__": "__codeact__",
        "call_tool": call_tool,
    }

    buffer = io.StringIO()
    ok = True
    error = ""
    try:
        with contextlib.redirect_stdout(buffer):
            exec(compile(code, "<codeact>", "exec"), globals_dict, {})  # noqa: S102
    except BaseException:  # noqa: BLE001 —— 沙箱里任何异常都要回传
        ok = False
        error = traceback.format_exc(limit=3)

    send({"op": "done", "ok": ok, "stdout": buffer.getvalue(), "error": error})
    return 0


if __name__ == "__main__":
    sys.exit(main())
