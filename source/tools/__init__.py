"""
DD-SJTUClaw tools 包。

对外暴露：
- Tool / ToolResult / ToolRegistry / 安全级别常量（base）
- build_default_registry：构造带默认只读 tool 的 registry
- 协议解析（protocol）：parse_model_output / build_tools_prompt
"""

from .base import (
    SAFETY_READ_ONLY,
    SAFETY_WRITE,
    SAFETY_SHELL,
    Tool,
    ToolResult,
    ToolRegistry,
)
from .readonly import register_readonly_tools
from .protocol import parse_model_output, build_tools_prompt, ParsedOutput


def build_default_registry():
    """构造一个注册了全部默认只读 tool 的 registry。"""
    registry = ToolRegistry()
    register_readonly_tools(registry)
    return registry


__all__ = [
    "SAFETY_READ_ONLY",
    "SAFETY_WRITE",
    "SAFETY_SHELL",
    "Tool",
    "ToolResult",
    "ToolRegistry",
    "register_readonly_tools",
    "build_default_registry",
    "parse_model_output",
    "build_tools_prompt",
    "ParsedOutput",
]
