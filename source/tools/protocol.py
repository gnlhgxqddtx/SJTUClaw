"""
Tool call 协议：模型只负责提出"要调用哪个 tool、参数是什么"或"输出最终回答"，
runtime 负责解析模型输出、执行 tool、把结果喂回模型。

采用结构化 JSON 协议（不依赖底层 API 是否支持原生 function calling）：
- 单个 tool call： {"type":"tool_call","tool":"read_file","args":{"path":"README.md"}}
- 多个 tool call： {"type":"tool_calls","calls":[{"tool":"current_time","args":{}}, ...]}   一轮最多 5 个
- 最终回答：       {"type":"final","content":"..."}

解析要求：
- 能区分 tool call 与 final answer；
- 模型在 JSON 前后混入解释文本时，尽量抽取其中合法的协议 JSON（不做随意字符串匹配）；
- 完全无法解析出协议 JSON 时，退化为把整段文本当作 final answer。
"""

import json
from dataclasses import dataclass, field

# 单轮最多执行的 tool call 数量。
MAX_TOOL_CALLS_PER_ROUND = 5


@dataclass
class ParsedOutput:
    kind: str                       # "tool_calls" / "final" / "use_skill"
    calls: list = field(default_factory=list)   # [{"tool": str, "args": dict}]
    content: str = ""
    raw: str = ""
    skill: str = ""                 # kind == "use_skill" 时的 skill 名称
    reason: str = ""                # kind == "use_skill" 时模型给出的选用理由


def _iter_json_objects(text: str):
    """从文本中依次扫描出顶层平衡的 {...} 片段并尝试解析为 JSON 对象。
    正确处理字符串字面量与转义，避免被字符串内的花括号干扰。"""
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    snippet = text[start:i + 1]
                    try:
                        yield json.loads(snippet)
                    except json.JSONDecodeError:
                        pass
                    start = -1


def _normalize_calls(raw_calls) -> list:
    """把协议里的 calls 规整成 [{"tool":str,"args":dict}]，并限制最多 5 个。"""
    calls = []
    if not isinstance(raw_calls, list):
        return calls
    for c in raw_calls:
        if not isinstance(c, dict):
            continue
        tool = c.get("tool")
        if not isinstance(tool, str) or not tool:
            continue
        args = c.get("args", {})
        if not isinstance(args, dict):
            args = {}
        calls.append({"tool": tool, "args": args})
        if len(calls) >= MAX_TOOL_CALLS_PER_ROUND:
            break
    return calls


def _as_protocol(obj) -> ParsedOutput | None:
    """把一个已解析的 JSON 对象尝试解释为协议对象；不是协议则返回 None。"""
    if not isinstance(obj, dict):
        return None
    t = obj.get("type")
    if t == "final":
        content = obj.get("content", "")
        return ParsedOutput(kind="final", content=str(content))
    if t == "tool_call":
        tool = obj.get("tool")
        if isinstance(tool, str) and tool:
            args = obj.get("args", {})
            if not isinstance(args, dict):
                args = {}
            return ParsedOutput(kind="tool_calls", calls=[{"tool": tool, "args": args}])
        return None
    if t == "tool_calls":
        calls = _normalize_calls(obj.get("calls"))
        if calls:
            return ParsedOutput(kind="tool_calls", calls=calls)
        return None
    if t == "use_skill":
        skill = obj.get("skill")
        if isinstance(skill, str) and skill:
            reason = obj.get("reason", "")
            return ParsedOutput(kind="use_skill", skill=skill, reason=str(reason))
        return None
    return None


def parse_model_output(text: str) -> ParsedOutput:
    """解析模型输出为协议对象。找不到合法协议 JSON 时退化为 final(raw)。"""
    raw = text or ""
    stripped = raw.strip()

    # 优先尝试整体就是一个 JSON 对象
    try:
        whole = json.loads(stripped)
        parsed = _as_protocol(whole)
        if parsed is not None:
            parsed.raw = raw
            return parsed
    except json.JSONDecodeError:
        pass

    # 否则从文本中扫描出内嵌的协议 JSON，取第一个合法的
    for obj in _iter_json_objects(stripped):
        parsed = _as_protocol(obj)
        if parsed is not None:
            parsed.raw = raw
            return parsed

    # 完全没有协议 JSON：把整段文本当作最终回答
    return ParsedOutput(kind="final", content=stripped, raw=raw)


def build_tools_prompt(definitions: list[dict]) -> str:
    """把 tool definitions + 调用协议组装成一段系统提示，注入上下文供模型参考。"""
    if not definitions:
        return ""
    lines = ["你可以调用以下工具来读取外部真实环境信息（不要凭空猜测）：", ""]
    for d in definitions:
        props = d.get("input_schema", {}).get("properties", {})
        required = d.get("input_schema", {}).get("required", [])
        arg_descs = []
        for pname, pinfo in props.items():
            mark = "必填" if pname in required else "可选"
            arg_descs.append(f"{pname}({mark}): {pinfo.get('description', '')}")
        args_text = "; ".join(arg_descs) if arg_descs else "无参数"
        lines.append(f"- {d['name']} [{d.get('safety_level', '')}]: {d['description']}")
        lines.append(f"    参数: {args_text}")
    lines += [
        "",
        "调用协议（你的每次回复都必须是且仅是一个合法 JSON 对象，不要输出多余文本）：",
        '- 调用一个工具: {"type":"tool_call","tool":"<name>","args":{...}}',
        '- 调用多个工具(一轮最多5个): {"type":"tool_calls","calls":[{"tool":"<name>","args":{...}}]}',
        '- 输出最终答案: {"type":"final","content":"<给用户的回答>"}',
        "",
        "你会在下一轮看到形如 [tool_result:<name>] 的工具执行结果（成功或失败）。"
        "请基于真实结果继续推理，需要更多信息就继续调用工具，信息足够就输出 final。"
        "工具失败时不要假装成功，应结合错误信息如实回答。",
    ]
    return "\n".join(lines)
