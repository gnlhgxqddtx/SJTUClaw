"""
DD-SJTUClaw 上下文组装模块
将稳定上下文（system prompt + soul、长期记忆）、可用 tool 说明、会话摘要
与当前会话历史统一组装为 LLM 输入。

稳定上下文边界：system prompt、soul、memory、tool 说明都以 system 角色置于会话历史之前，
每次请求都重新组装，因此不会被普通 session 对话历史覆盖。

组装顺序：system prompt -> soul -> memory -> tools 说明 -> session summary -> recent session messages。
前若干项合并为**单条** system 消息（兼容"system 消息必须在开头且仅一条"的服务端约束）。

角色映射：session 中存储的 tool result 使用 role="tool"（携带 trace 元数据）；
发送给 LLM API 时映射为 role="user" 并加 [tool_result:<name>] 前缀，
因为本项目使用 JSON 文本协议而非原生 function calling。
"""


def _format_memories(memories):
    lines = [f"- [{m['id']}] {m['content']}" for m in memories]
    return "以下是关于用户的长期记忆（稳定上下文，回答时请参考）：\n" + "\n".join(lines)


def _format_summary(session_summary):
    return "以下是本会话较早对话的压缩摘要（回答时请参考其中的任务状态与重要信息）：\n" + session_summary


def _to_api_message(msg):
    """把 session 中存储的一条消息转成 LLM API 可接受的 {role, content}。

    - user / assistant / system：原样保留 role 与 content；
    - tool（工具执行结果）：映射为 user 角色，并加 [tool_result:<name>] 前缀作为 observation；
    - 其它未知角色：也按 user 处理，避免服务端拒绝。
    仅保留 role 与 content 两个字段，trace 元数据（如 tool 名称）不发送给 API。
    """
    role = msg.get("role", "user")
    content = msg.get("content", "")
    if role in ("user", "assistant", "system"):
        return {"role": role, "content": content}
    if role == "tool":
        name = msg.get("tool", "")
        prefix = f"[tool_result:{name}]" if name else "[tool_result]"
        return {"role": "user", "content": f"{prefix}\n{content}"}
    return {"role": "user", "content": content}


def build_messages(stable_prompt, memories, session_summary, session_messages, tools_prompt=None):
    """
    组装发送给模型的完整消息列表。

    Args:
        stable_prompt: system prompt 与 soul 合并后的稳定系统提示文本
        memories: 长期记忆列表，元素形如 {"id", "content", "createdAt"}
        session_summary: 本会话较早消息压缩得到的摘要（可为空字符串）
        session_messages: 当前会话的消息（user / assistant / tool ...）
        tools_prompt: 可用 tool 说明 + 调用协议（可为空字符串/None）

    Returns:
        list[dict]: [单条稳定系统提示(含 memory / tools / summary), *会话消息(已做角色映射)]
    """
    parts = []
    if stable_prompt:
        parts.append(stable_prompt)
    if memories:
        parts.append(_format_memories(memories))
    if tools_prompt:
        parts.append(tools_prompt)
    if session_summary:
        parts.append(_format_summary(session_summary))

    messages = []
    if parts:
        messages.append({"role": "system", "content": "\n\n".join(parts)})
    messages.extend(_to_api_message(m) for m in session_messages)
    return messages
