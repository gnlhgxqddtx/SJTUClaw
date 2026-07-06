"""
DD-SJTUClaw 主程序
与 SJTU LLM API 交互的入口程序，默认使用 GLM-5.1 模型。
"""

import sys
import os

# 确保可以导入同目录的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm_client import LLMClient
from config import SUPPORTED_MODELS, DEFAULT_MODEL


def test_api_call():
    """测试 API 调用是否正常工作"""
    print(f"=== DD-SJTUClaw LLM API 测试 ===")
    print(f"默认模型: {DEFAULT_MODEL} ({SUPPORTED_MODELS.get(DEFAULT_MODEL, '未知')})")
    print()

    # 创建客户端
    client = LLMClient(model=DEFAULT_MODEL)
    print("✓ 客户端初始化成功\n")

    # 测试1: 基本对话
    print("--- 测试1: 基本对话 ---")
    question = "请用一句话介绍你自己。"
    print(f"提问: {question}")
    try:
        reply = client.chat(question)
        print(f"回复: {reply}")
        print("✓ 基本对话测试通过\n")
    except Exception as e:
        print(f"✗ 基本对话测试失败: {e}\n")
        return False

    # 测试2: 流式输出
    print("--- 测试2: 流式输出 ---")
    question = "请用三句话描述春天。"
    print(f"提问: {question}")
    print("回复: ", end="", flush=True)
    try:
        for chunk in client.chat_stream(question):
            print(chunk, end="", flush=True)
        print()
        print("✓ 流式输出测试通过\n")
    except Exception as e:
        print(f"\n✗ 流式输出测试失败: {e}\n")
        return False

    # 测试3: 多轮对话模拟
    print("--- 测试3: 英文问答 ---")
    question = "What is the capital of France?"
    print(f"提问: {question}")
    try:
        reply = client.chat(question, system_prompt="You are a helpful assistant. Reply in English.")
        print(f"回复: {reply}")
        print("✓ 英文问答测试通过\n")
    except Exception as e:
        print(f"✗ 英文问答测试失败: {e}\n")
        return False

    print("=" * 40)
    print("所有测试通过！API 调用正常工作。")
    return True


if __name__ == "__main__":
    success = test_api_call()
    sys.exit(0 if success else 1)
