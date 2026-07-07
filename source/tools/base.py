"""
统一的 Tool 数据结构与 Tool Registry。

一个 tool 分为两部分：
- tool definition：给模型看的说明（name / description / input_schema / safety_level）。
- tool handler：runtime 中真正执行的函数，模型不能直接执行。

Registry 负责注册、查找、列出 definitions，并根据 name 执行对应 handler，
返回统一格式的 ToolResult；执行前会按 input_schema 做基础参数校验，而不是直接信任模型输出。
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# 安全级别：Step 5 只实现只读 tool；写/命令类 tool 在 Step 8 引入。
SAFETY_READ_ONLY = "read_only"
SAFETY_WRITE = "write"
SAFETY_SHELL = "shell"


@dataclass
class ToolResult:
    """统一的 tool 执行结果。成功与失败都用它表达，便于反馈回 agent loop。"""

    ok: bool
    tool: str
    output: str = ""
    error: str = ""
    extra: dict = field(default_factory=dict)

    def to_observation(self) -> str:
        """转成一段供模型阅读的 observation 文本。"""
        if self.ok:
            return self.output
        return f"[error] {self.error}"

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "output": self.output,
            "error": self.error,
            "extra": self.extra,
        }


@dataclass
class Tool:
    """给模型看的 tool 说明 + runtime 真正执行的 handler。"""

    name: str
    description: str
    input_schema: dict          # 形如 {"type":"object","properties":{...},"required":[...]}
    handler: Callable[[dict], ToolResult]
    safety_level: str = SAFETY_READ_ONLY

    def definition(self) -> dict:
        """导出给模型看的 definition（不含 handler）。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "safety_level": self.safety_level,
        }


class ToolRegistry:
    """保存 tool definition + handler，按 name 查找、列出、执行。"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool 已存在: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def definitions(self) -> list[dict]:
        """列出全部 tool definition，供模型判断该调用哪个 tool。"""
        return [t.definition() for t in self._tools.values()]

    def _validate_args(self, tool: Tool, args: Any) -> Optional[str]:
        """按 input_schema 做基础校验：args 必须是 dict，required 字段必须存在。
        返回错误信息字符串；校验通过返回 None。"""
        if not isinstance(args, dict):
            return "参数必须是一个 JSON 对象"
        required = tool.input_schema.get("required", [])
        missing = [k for k in required if k not in args]
        if missing:
            return f"缺少必需参数: {', '.join(missing)}"
        return None

    def execute(self, name: str, args: Any) -> ToolResult:
        """按 name 找到 tool 并执行 handler。runtime 校验参数、捕获异常，
        始终返回统一的 ToolResult（不会因 tool 失败而抛出到 agent loop 之外）。"""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, tool=name, error=f"未知的 tool: {name}")

        err = self._validate_args(tool, args)
        if err is not None:
            return ToolResult(ok=False, tool=name, error=err)

        try:
            result = tool.handler(args)
        except Exception as e:  # handler 内部异常也转成失败结果反馈给模型
            return ToolResult(ok=False, tool=name, error=f"tool 执行异常: {e}")

        if not isinstance(result, ToolResult):
            return ToolResult(ok=False, tool=name, error="tool handler 返回值格式非法")
        return result
