"""
DD-SJTUClaw 上下文压缩（compaction）模块

作用：当单个 session 的对话历史过长时，把较早的消息压缩成一段 summary，
只保留最近 N 条原始消息，从而控制发送给模型的上下文长度。

边界：只压缩 session.messages（user / assistant 对话历史），
不处理 system prompt、soul、memory —— 它们是稳定上下文，由 context_builder 单独组装。

仅使用 Python 标准库（LLM 调用通过传入的 client 完成）。
"""

from datetime import datetime


# 生成摘要时给模型的指令：明确要保留与要删除的内容
_SUMMARY_INSTRUCTION = (
    "你是对话摘要助手。请把下面这些较早的对话（如有『已有摘要』也一并纳入）"
    "压缩成一段简洁、连贯的中文摘要。\n"
    "必须保留：当前任务、已完成的内容、用户的要求、尚未解决的问题、重要事实。\n"
    "必须删除：寒暄、客套、重复表达、与任务无关的细节。\n"
    "只输出摘要正文本身，不要输出多余的解释或前后缀。"
)


def _messages_char_count(messages):
    return sum(len(m.get("content", "")) for m in messages)


def should_compact(session, max_messages, max_chars):
    """判断是否需要对该 session 触发压缩。

    触发策略（满足其一即触发）：
      1. session.messages 的条数 > max_messages；
      2. session.messages 所有 content 的总字符数 > max_chars。
    两者均未超过阈值时返回 False（不触发）。
    """
    messages = session.messages
    if len(messages) > max_messages:
        return True
    if _messages_char_count(messages) > max_chars:
        return True
    return False


def _build_summary_prompt(existing_summary, old_messages):
    """把已有摘要 + 待压缩的旧消息组装成一次摘要请求。"""
    convo_lines = [f"{m['role']}: {m['content']}" for m in old_messages]
    user_content = ""
    if existing_summary:
        user_content += f"【已有摘要】\n{existing_summary}\n\n"
    user_content += "【较早对话】\n" + "\n".join(convo_lines)
    return [
        {"role": "system", "content": _SUMMARY_INSTRUCTION},
        {"role": "user", "content": user_content},
    ]


def compact(client, session, recent_n):
    """对 session 执行一次压缩。

    流程：
      - 保留最近 recent_n 条原始消息，更早的消息作为待压缩内容；
      - 将『已有摘要 + 待压缩消息』交给 LLM 生成新摘要；
      - 新摘要非空才应用：写入 session.summary，并把 messages 截断为最近 recent_n 条。

    失败保护：
      - 待压缩消息不足（消息数 <= recent_n）：不调用 LLM，不改动，applied=False；
      - LLM 调用失败：不删除任何旧消息，applied=False；
      - 生成的摘要为空/无效：不应用本次结果，applied=False。

    返回 dict：
      {"applied", "old_count", "recent_count", "summary", "reason"}
    调用方负责在 applied 为 True 时持久化 session。
    """
    messages = session.messages
    if len(messages) <= recent_n:
        return {
            "applied": False,
            "old_count": 0,
            "recent_count": len(messages),
            "summary": session.summary,
            "reason": "消息数不足，无需压缩",
        }

    old_messages = messages[:-recent_n]
    recent_messages = messages[-recent_n:]

    try:
        new_summary = client.chat(_build_summary_prompt(session.summary, old_messages))
    except Exception as e:
        # compaction 调用失败：不删除旧 messages
        return {
            "applied": False,
            "old_count": len(old_messages),
            "recent_count": len(recent_messages),
            "summary": session.summary,
            "reason": f"摘要生成失败: {e}",
        }

    new_summary = (new_summary or "").strip()
    if not new_summary:
        # summary 为空或无效：不应用本次 compaction 结果
        return {
            "applied": False,
            "old_count": len(old_messages),
            "recent_count": len(recent_messages),
            "summary": session.summary,
            "reason": "摘要为空或无效",
        }

    session.summary = new_summary
    session.messages = recent_messages
    session.updated_at = datetime.now()
    return {
        "applied": True,
        "old_count": len(old_messages),
        "recent_count": len(recent_messages),
        "summary": new_summary,
        "reason": "",
    }
