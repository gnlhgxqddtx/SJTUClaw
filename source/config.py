"""
DD-SJTUClaw 配置模块
存放 API 相关配置信息，API Key 从项目根目录的 .env 文件读取，避免泄露。
"""

import os

from dotenv import load_dotenv

# 项目根目录为本文件所在的 source/ 目录的上一级
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 加载项目根目录下的 .env 文件
_ENV_FILE = os.path.join(_PROJECT_ROOT, ".env")
load_dotenv(_ENV_FILE)

# API 基础地址（可通过 .env 中的 LLM_BASE_URL 覆盖）
API_BASE_URL = os.getenv("LLM_BASE_URL", "https://models.sjtu.edu.cn/api/v1")

# 支持的模型列表
SUPPORTED_MODELS = {
    "deepseek-chat": "DeepSeek V3.2 (常规模式)",
    "deepseek-reasoner": "DeepSeek V3.2 (思考模式)",
    "minimax": "MiniMax-M2.7",
    "glm": "GLM-5.1",
    "qwen": "Qwen3.5-27B",
}

# 默认使用的模型（可通过 .env 中的 LLM_MODEL 覆盖）
DEFAULT_MODEL = os.getenv("LLM_MODEL", "glm")


def load_api_key() -> str:
    """从 .env 文件（环境变量 LLM_API_KEY）加载 API Key"""
    key = os.getenv("LLM_API_KEY", "").strip()
    if not key:
        raise ValueError(
            f"未能读取到 API Key。\n"
            f"请在 {_ENV_FILE} 中设置 LLM_API_KEY=<你的 API Key>。"
        )
    return key
