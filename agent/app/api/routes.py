"""HTTP 接口层。

只有四个接口：

    GET  /agent/health                  健康检查
    POST /agent/chat                    SSE 对话流（核心）
    POST /agent/approvals/{id}/decide   审批决议
    GET  /agent/runs/{run_id}           内存里的运行事件快照（调试用）

**认证模型**：这些接口只接受 Go 后端的调用。
Go 会在 `X-Internal-Token` 里带上共享密钥，并转发已认证的用户信息
（`X-User-Id` / `X-User-Scopes` / `X-User-Token`）。

这样浏览器永远不直接访问 8090，鉴权边界仍然只有一个（在 Go 那边）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..execenv.runtime import APPROVALS, CLARIFICATIONS, Runtime, UserContext
from ..orchestration.supervisor import Supervisor
from ..protocol.agui import AgUiEncoder

router = APIRouter()

# 内存里的活跃/近期运行。用于进程内的审批关联与调试，不承担持久化职责
# （持久化的 trace 在 MySQL 里，由 tracer.py 写）。
RUNS: dict[str, Runtime] = {}

# ---------------------------------------------------------------------------
# 代码指纹
#
# 为什么要它：改完代码忘了重启服务，排查时会一直看旧行为，
# 白白怀疑自己。`/agent/health` 把这个值露出来，一眼就能判断
# "现在跑的是不是最新代码"。
# ---------------------------------------------------------------------------
def _compute_revision() -> str:
    import hashlib
    import time as _time
    from pathlib import Path

    digest = hashlib.sha1()
    root = Path(__file__).resolve().parent.parent
    try:
        for path in sorted(root.rglob("*.py")):
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}".encode())
    except OSError:
        return "unknown"
    return f"{digest.hexdigest()[:8]}-{int(_time.time())}"


def _time_iso() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


REVISION = _compute_revision()
STARTED_AT = _time_iso()

_MAX_RUNS = 64


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    model: str = Field(default="", max_length=64)
    # AG-UI 的 threadId。空表示服务端新开一个会话。
    thread_id: str = Field(default="", max_length=32, alias="threadId")
    history: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ApprovalRequest(BaseModel):
    approved: bool


class ClarifyAnswerRequest(BaseModel):
    """用户对澄清弹窗的回答。

    `text` 是自由文本 —— 用户可能点选项（前端把选项文案发上来），
    也可能自己打一句话。识别层两种都能理解。
    """

    text: str = Field(default="", max_length=2000)


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
async def require_identity(
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> UserContext:
    """校验内部密钥并构造用户上下文。

    这是本服务唯一的入口校验：只认 Go 后端转发过来的请求。
    """
    settings = get_settings()
    token = x_internal_token or request.headers.get("X-Internal-Token")
    if token != settings.internal_token:
        raise HTTPException(status_code=401, detail="internal token 不匹配")

    raw_user_id = request.headers.get("X-User-Id")
    if not raw_user_id:
        raise HTTPException(status_code=401, detail="缺少用户身份")
    try:
        user_id = int(raw_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="用户 ID 不合法") from exc

    scopes_raw = request.headers.get("X-User-Scopes") or ""
    scopes = tuple(item.strip() for item in scopes_raw.split(",") if item.strip())

    return UserContext(
        user_id=user_id,
        username=request.headers.get("X-Username") or "",
        real_name=request.headers.get("X-Real-Name") or "",
        scopes=scopes,
        token=request.headers.get("X-User-Token") or "",
    )


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
@router.get("/health")
async def health() -> dict[str, Any]:
    from .. import db
    from ..tools.policy import CODE_CHANNEL_AGENTS
    from ..tools.registry import REGISTRY

    settings = get_settings()
    db_ok, db_error = db.ping()

    return {
        "ok": db_ok,
        "model": settings.default_model,
        "database": {"ok": db_ok, "error": db_error},
        "tools": REGISTRY.stats(),
        # 双通道架构的运行时状态：这是项目的核心设计，必须可观测
        "code_channel": {
            "enabled": settings.enable_code_channel,
            "agents": sorted(CODE_CHANNEL_AGENTS),
            "sandbox_tools": len([t for t in REGISTRY.all() if t.visible_in_sandbox()]),
            "sandbox": "subprocess",
        },
        "agents": ["query", "compute", "execute", "notify"],
        "active_runs": len(RUNS),
        # 正在跑的运行此刻停在哪个阶段、已经多久没有新事件。
        # 卡住的时候直接看这里，比猜有用得多。
        "runs": [
            {
                "run_id": item.run_id,
                "stage": item.stage,
                "idle_s": round(item.bus.idle_seconds(), 1),
                "awaiting_approval": item.awaiting_approval,
                "closed": item.bus.closed,
            }
            for item in list(RUNS.values())[-5:]
        ],
        "pending_approvals": APPROVALS.pending_count,
        # 待处理的审批 id：前端面板万一没弹，可以拿这个手动提交决议
        "approvals": APPROVALS.snapshot(),
        # 代码指纹 + 启动时间。改了代码没重启进程时这里还是旧值 ——
        # 排查"为什么我的修复没生效"，第一个要看的就是它。
        "revision": REVISION,
        "started_at": STARTED_AT,
        # 这些上限值决定了"最长会卡多久"
        "limits": {
            "max_wall_seconds": settings.max_wall_seconds,
            "max_llm_calls": settings.max_llm_calls,
            "max_agent_steps": settings.max_agent_steps,
            "approval_timeout_s": settings.approval_timeout_s,
            "stream_idle_timeout_s": settings.stream_idle_timeout_s,
        },
    }


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    x_llm_api_key: str | None = Header(default=None, alias="X-LLM-Api-Key"),
) -> StreamingResponse:
    """核心接口：一次对话 = 一条 SSE 事件流。"""
    user = await require_identity(request, request.headers.get("X-Internal-Token"))

    settings = get_settings()
    api_key = (x_llm_api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="缺少 DeepSeek API Key")

    model = (payload.model or settings.default_model).strip()

    # 同一会话不允许两个运行同时进行。
    #
    # 为什么必须挡：两轮运行会并发地调工具，而工具在线程池里执行。
    # 早期 DB 层是全局共享连接，两轮重叠会把 MySQL 协议包搞串，
    # 表现就是"卡住不动"。现在连接已经按线程隔离，但重叠运行依然是
    # 语义错误（同一个 thread 上两条回复会互相穿插），所以这里也挡掉。
    requested_thread = (payload.thread_id or "").strip()
    if requested_thread:
        for existing in RUNS.values():
            if existing.thread_id == requested_thread and not existing.bus.closed:
                raise HTTPException(
                    status_code=409,
                    detail="该会话还有一次运行没结束，请等它完成或先点停止",
                )

    runtime = Runtime(
        goal=payload.message,
        user=user,
        model=model,
        api_key=api_key,
        settings=settings,
        thread_id=payload.thread_id,
    )
    _remember(runtime)

    # 历史优先从库里取：刷新页面后前端不必自己维护上下文，
    # 换设备继续聊也能接上。
    history = payload.history
    if not history and payload.thread_id:
        from ..memory.conversation import load_history

        history = load_history(
            thread_id=runtime.thread_id, user_id=user.user_id, limit=20
        )

    supervisor = Supervisor(settings)
    task = asyncio.create_task(
        supervisor.run(runtime, message=payload.message, history=history)
    )
    # 异常已经在 supervisor 内部转成 error 事件；这里只是防止未捕获告警
    task.add_done_callback(_swallow_task_exception)

    # **看门狗**：独立于主流程。
    # 前面那些超时都要求"能走到检查点"，而真正卡死时一个检查点都走不到。
    # 这个任务只盯"是不是很久没有新事件"，到点就强行收尾并给出兜底回答。
    watchdog = asyncio.create_task(runtime.enforce_stall(task, stall_s=60.0))
    watchdog.add_done_callback(_swallow_task_exception)

    # AG-UI 编码器：内部事件 → 标准协议事件
    encoder = AgUiEncoder(thread_id=runtime.thread_id, run_id=runtime.run_id)

    async def stream() -> Any:
        # 先发 RUN_STARTED + 初始状态快照，前端拿到快照就能建好状态树，
        # 后面的增量补丁才能正确应用。
        for event in encoder.start():
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield (
            "data: "
            + json.dumps(
                {
                    "type": "CUSTOM",
                    "name": "courseflow.thread",
                    "value": {"threadId": runtime.thread_id, "runId": runtime.run_id},
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )

        cursor = 0
        finished = False
        # 硬截止：即使业务侧完全卡住（比如某个 await 永不返回），
        # 这条流也必须在合理时限后自己结束，否则前端会一直转圈。
        # 这里刻意给得比业务墙钟宽：审批等待、多次确认都要留余量，
        # 真正的边界是 Governance.check_wall()（它会把审批时间扣掉）。
        deadline = (
            asyncio.get_running_loop().time()
            + settings.max_wall_seconds * 2
            + settings.approval_timeout_s * 2
            + 60
        )
        last_beat_idle = 0.0
        try:
            while True:
                events = runtime.bus.snapshot_from(cursor)
                for event in events:
                    for out in encoder.encode(event):
                        if out.get("type") == "RUN_FINISHED":
                            finished = True
                        yield f"data: {json.dumps(out, ensure_ascii=False)}\n\n"
                cursor += len(events)

                if runtime.bus.closed and cursor >= runtime.bus.length:
                    break

                # 心跳：长时间没有事件时告诉前端"还活着，在等什么"。
                # 没有这个，界面上的活动条会看起来像死机 ——
                # 用户无法区分"模型在想"和"进程挂了"。
                idle = runtime.bus.idle_seconds()
                if not task.done() and idle > 8 and idle - last_beat_idle > 6:
                    last_beat_idle = idle
                    beat = {
                        "type": "CUSTOM",
                        "name": "courseflow.heartbeat",
                        "value": {
                            "stage": runtime.stage,
                            "idle_s": round(idle, 1),
                        },
                    }
                    yield f"data: {json.dumps(beat, ensure_ascii=False)}\n\n"

                if asyncio.get_running_loop().time() > deadline:
                    # 强制收尾：发一条错误 + 结束，别让前端无限等
                    task.cancel()
                    for out in encoder.encode(
                        {
                            "kind": "error",
                            "message": f"运行超时（{int(settings.max_wall_seconds)}s），已中止",
                        }
                    ):
                        yield f"data: {json.dumps(out, ensure_ascii=False)}\n\n"
                    for out in encoder.finish(status="failed"):
                        yield f"data: {json.dumps(out, ensure_ascii=False)}\n\n"
                    finished = True
                    break

                if await request.is_disconnected():
                    task.cancel()
                    break

                # 30ms 轮询：更细的粒度能让流式文本更顺滑。
                # 再小就是无意义的空转（模型的 token 本身也不是每毫秒一个）。
                await asyncio.sleep(0.03)

            if not finished:
                for out in encoder.finish(status="ok"):
                    yield f"data: {json.dumps(out, ensure_ascii=False)}\n\n"
        finally:
            # 客户端断开时也别让 agent 继续烧钱
            if not task.done():
                task.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/approvals/{approval_id}/decide")
async def decide_approval(
    approval_id: str,
    payload: ApprovalRequest,
    request: Request,
) -> dict[str, Any]:
    """前端点了「允许」或「拒绝」之后，Go 转发到这里。"""
    await require_identity(request, request.headers.get("X-Internal-Token"))

    ok = APPROVALS.decide(approval_id, payload.approved)
    if not ok:
        raise HTTPException(status_code=404, detail="审批已失效或不存在")
    return {"ok": True, "approved": payload.approved}


@router.post("/clarifications/{clarification_id}/answer")
async def answer_clarification(
    clarification_id: str,
    payload: ClarifyAnswerRequest,
    request: Request,
) -> dict[str, Any]:
    """前端在澄清弹窗里作答之后，Go 转发到这里。

    这个接口一返回，**挂起的那个运行会自己继续往下跑**（同一轮、同一个 run），
    不需要前端再发一次 `/agent/chat`。
    """
    await require_identity(request, request.headers.get("X-Internal-Token"))

    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="回答不能为空")

    ok = CLARIFICATIONS.answer(clarification_id, text)
    if not ok:
        raise HTTPException(status_code=404, detail="澄清已失效或已超时")
    return {"ok": True, "text": text}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    """内存里的事件快照。持久化版本请查 Go 的 trace 接口。"""
    await require_identity(request, request.headers.get("X-Internal-Token"))

    runtime = RUNS.get(run_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="运行不存在或已过期")
    return {
        "run_id": run_id,
        "events": runtime.bus.history_snapshot(),
        "governance": runtime.governance.snapshot(),
    }


# ---------------------------------------------------------------------------
def _remember(runtime: Runtime) -> None:
    RUNS[runtime.run_id] = runtime
    # 简单淘汰：超过上限就丢最早的。这里不需要精确 LRU。
    while len(RUNS) > _MAX_RUNS:
        oldest = next(iter(RUNS))
        RUNS.pop(oldest, None)


def _swallow_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:  # noqa: BLE001
        pass