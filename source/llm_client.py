"""
DD-SJTUClaw LLM 客户端模块
基于 OpenAI 兼容协议封装 LLM API 调用。
"""

from typing import Optional

from openai import OpenAI

from .config import API_BASE_URL, load_api_key, DEFAULT_MODEL


class LLMClient:
    """与大语言模型交互的客户端"""

    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_MODEL):
        """
        初始化 LLM 客户端

        Args:
            api_key: API Key，若不提供则从 .env 自动读取
            model: 模型名称，默认取自 .env 的 LLM_MODEL
        """
        self.api_key = api_key or load_api_key()
        self.model = model
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=API_BASE_URL,
        )

    def chat(self, messages: list[dict]) -> str:
        """
        发送完整会话历史给模型并获取回复（非流式）

        Args:
            messages: 消息列表，元素形如 {"role": "user"/"assistant"/"system", "content": str}

        Returns:
            模型的回复文本
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )
        return response.choices[0].message.content

    def chat_stream(self, messages: list[dict]):
        """
        以流式方式发送完整会话历史给模型，逐 token 输出

        Args:
            messages: 消息列表，元素形如 {"role": ..., "content": ...}

        Yields:
            模型回复的文本片段
        """
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta is not None:
                yield delta
