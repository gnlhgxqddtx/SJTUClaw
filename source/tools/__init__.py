"""
DD-SJTUClaw tools 包。

对外暴露：
- Tool / ToolResult / ToolRegistry / ToolContext / 安全级别常量（base）
- build_default_registry：构造带默认只读 tool + 高级 tool 的 registry
- 协议解析（protocol）：parse_model_output / build_tools_prompt
"""

from .base import (
    SAFETY_DOWNLOAD,
    SAFETY_READ_ONLY,
    SAFETY_SHELL,
    SAFETY_WRITE,
    Tool,
    ToolContext,
    ToolResult,
    ToolRegistry,
)
from .readonly import register_readonly_tools
from .advanced import register_advanced_tools
from .protocol import parse_model_output, build_tools_prompt, ParsedOutput


def build_default_registry():
    """构造一个注册了默认只读 tool 与高级 tool（update / shell / download / 附件拷贝）的 registry。
    高级 tool 的执行需 approval，且都要求已设置 workspace。"""
    registry = ToolRegistry()
    register_readonly_tools(registry)
    register_advanced_tools(registry)
    return registry


__all__ = [
    "SAFETY_READ_ONLY",
    "SAFETY_WRITE",
    "SAFETY_SHELL",
    "SAFETY_DOWNLOAD",
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolRegistry",
    "register_readonly_tools",
    "register_advanced_tools",
    "build_default_registry",
    "parse_model_output",
    "build_tools_prompt",
    "ParsedOutput",
]
