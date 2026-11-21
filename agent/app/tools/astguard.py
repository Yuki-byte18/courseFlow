"""CodeAct 的第一道闸：AST 白名单校验。

思路：**先证明代码"只会做计算"，再允许它运行**。
不是黑名单（拉黑 `import os` 之后还有一百种绕过方式），而是白名单：
不认识的语法节点直接拒绝。

这一层只是"静态检查"。真正跑起来之后还有进程隔离（sandbox.py）兜底 ——
两层防护的原因：静态检查可能被绕过，进程隔离可能被资源耗尽；
两者叠加才够。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


class SandboxViolation(ValueError):
    """代码违反沙箱规则。文案会原样回给模型，让它自己改。"""


# ---------------------------------------------------------------------------
# 允许的语法节点（白名单）
#
# 不在这个集合里的一律拒绝。刻意保留注释说明"为什么允许"，
# 否则以后没人敢往里加东西。
# ---------------------------------------------------------------------------
ALLOWED_NODES: frozenset[type[ast.AST]] = frozenset(
    {
        # 顶层与表达式
        ast.Module,
        ast.Expr,
        ast.Pass,
        ast.Constant,
        ast.Name,
        ast.Load,
        ast.Store,
        ast.Del,
        # 运算与比较
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.IfExp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Not,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.In,
        ast.NotIn,
        ast.Is,
        ast.IsNot,
        # 容器与下标
        ast.List,
        ast.Tuple,
        ast.Set,
        ast.Dict,
        ast.Subscript,
        ast.Slice,
        ast.Starred,
        ast.Attribute,
        # 调用
        ast.Call,
        ast.keyword,
        # 赋值
        ast.Assign,
        ast.AugAssign,
        ast.AnnAssign,
        # 控制流
        ast.If,
        ast.For,
        ast.While,
        ast.Break,
        ast.Continue,
        ast.Return,
        ast.Try,
        ast.ExceptHandler,
        ast.Raise,
        ast.Assert,
        # 函数
        ast.FunctionDef,
        ast.arguments,
        ast.arg,
        ast.Lambda,
        # 推导式
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.comprehension,
        # 字符串格式化
        ast.JoinedStr,
        ast.FormattedValue,
    }
)

# 明确拒绝并给出原因的高危节点（比"不在白名单"更友好的报错）
DENIED_NODES: dict[type[ast.AST], str] = {
    ast.Import: "不允许 import：代码通道只能做纯计算，业务数据请用 call_tool",
    ast.ImportFrom: "不允许 import：代码通道只能做纯计算，业务数据请用 call_tool",
    ast.ClassDef: "不允许定义类",
    ast.Global: "不允许 global",
    ast.Nonlocal: "不允许 nonlocal",
    ast.With: "不允许 with 语句",
    ast.AsyncWith: "不允许 async with",
    ast.AsyncFor: "不允许 async for",
    ast.AsyncFunctionDef: "不允许定义 async 函数",
    ast.Await: "不允许 await",
    ast.Yield: "不允许 yield",
    ast.YieldFrom: "不允许 yield from",
}

# 禁止作为调用目标的裸名字
DENIED_CALL_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "globals",
        "locals",
        "vars",
        "dir",
        "input",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "help",
        "exit",
        "quit",
        "memoryview",
        "type",
        "super",
        "object",
        "classmethod",
        "staticmethod",
        "property",
    }
)

# 禁止访问的属性名（对象图逃逸面）
DENIED_ATTRS: frozenset[str] = frozenset(
    {
        "__class__",
        "__bases__",
        "__subclasses__",
        "__mro__",
        "__globals__",
        "__code__",
        "__closure__",
        "__builtins__",
        "__dict__",
        "__getattribute__",
        "__getattr__",
        "__setattr__",
        "__reduce__",
        "__reduce_ex__",
        "__init_subclass__",
        "gi_frame",
        "cr_frame",
        "f_globals",
        "f_locals",
        "f_back",
    }
)

# 允许在沙箱里直接使用的内置函数
ALLOWED_BUILTINS: frozenset[str] = frozenset(
    {
        "print",
        "len",
        "range",
        "enumerate",
        "zip",
        "sorted",
        "reversed",
        "sum",
        "min",
        "max",
        "abs",
        "round",
        "any",
        "all",
        "int",
        "float",
        "str",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "frozenset",
        "isinstance",
        "repr",
        "format",
        "divmod",
        "map",
        "filter",
        "chr",
        "ord",
        "True",
        "False",
        "None",
    }
)


@dataclass
class GuardReport:
    """校验结果。ok=False 时 reason 可以直接回给模型。"""

    ok: bool
    reason: str = ""
    call_tool_names: tuple[str, ...] = ()


def validate(code: str) -> GuardReport:
    """校验一段 CodeAct 代码。

    返回 GuardReport 而不是抛异常：调用方要把 reason 回灌给模型，
    异常在跨进程/跨层传递时容易丢信息。
    """
    if not code.strip():
        return GuardReport(False, "代码为空")

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return GuardReport(False, f"语法错误：第 {exc.lineno} 行 {exc.msg}")

    call_tools: list[str] = []

    for node in ast.walk(tree):
        node_type = type(node)

        # 1. 高危节点给出明确的拒绝原因
        if node_type in DENIED_NODES:
            return GuardReport(False, f"第 {getattr(node, 'lineno', '?')} 行：{DENIED_NODES[node_type]}")

        # 2. 白名单之外的节点一律拒绝
        if node_type not in ALLOWED_NODES:
            return GuardReport(
                False,
                f"第 {getattr(node, 'lineno', '?')} 行：不允许的语法 {node_type.__name__}",
            )

        # 3. 禁止以裸名字调用危险函数
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in DENIED_CALL_NAMES:
                return GuardReport(False, f"第 {node.lineno} 行：不允许调用 {node.func.id}()")

        # 4. 禁止访问逃逸属性
        if isinstance(node, ast.Attribute):
            if node.attr in DENIED_ATTRS:
                return GuardReport(False, f"第 {node.lineno} 行：不允许访问 .{node.attr}")
            if node.attr.startswith("__") and node.attr.endswith("__"):
                return GuardReport(False, f"第 {node.lineno} 行：不允许访问 dunder 属性 .{node.attr}")

        # 5. 记录 call_tool 的目标工具名（供日志与审计，不参与判定）
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "call_tool"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            call_tools.append(node.args[0].value)

    # 6. 必须真的 print 点什么 —— 否则模型写了代码但用户什么也看不到
    if not call_tools and "print" not in code:
        return GuardReport(
            False,
            "代码里既没有 call_tool 也没有 print：这样执行不会有任何输出，请打印最终结论",
        )

    return GuardReport(True, "", tuple(call_tools))
