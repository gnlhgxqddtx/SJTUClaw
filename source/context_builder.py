"""
DD-SJTUClaw 上下文组装模块
将稳定上下文（system prompt + soul、长期记忆）与当前会话历史统一组装为 LLM 输入。

稳定上下文边界：system prompt、soul、memory 都以 system 角色置于会话历史之前，
每次请求都重新组装，因此不会被普通 session 对话历史覆盖。
"""


def _format_memories(memories):
    lines = [f"- [{m['id']}] {m['content']}" for m in memories]
    return "以下是关于用户的长期记忆（稳定上下文，回答时请参考）：\n" + "\n".join(lines)


def build_messages(stable_prompt, memories, session_messages):
    """
    组装发送给模型的完整消息列表。

    稳定上下文（system prompt + soul + 长期记忆）合并为**单条** system 消息置于最前，
    以兼容"system 消息必须在开头且仅一条"的服务端约束；随后接当前会话历史。
    每次请求都重新组装，因此稳定上下文不会被普通 session 对话历史覆盖。

    Args:
        stable_prompt: system prompt 与 soul 合并后的稳定系统提示文本
        memories: 长期记忆列表，元素形如 {"id", "content", "createdAt"}
        session_messages: 当前会话的历史消息（仅 user / assistant）

    Returns:
        list[dict]: [稳定系统提示(含长期记忆), *会话历史]
    """
    parts = []
    if stable_prompt:
        parts.append(stable_prompt)
    if memories:
        parts.append(_format_memories(memories))

    messages = []
    if parts:
        messages.append({"role": "system", "content": "\n\n".join(parts)})
    messages.extend(session_messages)
    return messages
