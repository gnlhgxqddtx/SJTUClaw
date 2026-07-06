"""
DD-SJTUClaw LLM 客户端模块
基于 OpenAI 兼容协议封装 LLM API 调用。
"""

from typing import Optional

from openai import OpenAI

from config import API_BASE_URL, load_api_key, DEFAULT_MODEL


class LLMClient:
    """与大语言模型交互的客户端"""

    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_MODEL):
        """
        初始化 LLM 客户端

        Args:
            api_key: API Key，若不提供则从本地文件自动读取
            model: 模型名称，默认为 glm (GLM-5.1)
        """
        self.api_key = api_key or load_api_key()
        self.model = model
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=API_BASE_URL,
        )

    def chat(self, message: str, system_prompt: str = "你是一个有帮助的助手。") -> str:
        """
        发送消息给模型并获取回复

        Args:
            message: 用户消息
            system_prompt: 系统提示词

        Returns:
            模型的回复文本
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
        )
        return response.choices[0].message.content

    def chat_stream(self, message: str, system_prompt: str = "你是一个有帮助的助手。"):
        """
        以流式方式发送消息给模型，逐 token 输出

        Args:
            message: 用户消息
            system_prompt: 系统提示词

        Yields:
            模型回复的文本片段
        """
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta is not None:
                yield delta
