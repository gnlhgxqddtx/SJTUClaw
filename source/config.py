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

# 会话持久化存储目录（项目根目录下 data/sessions/）
SESSIONS_DIR = os.path.join(_PROJECT_ROOT, "data", "sessions")

# system prompt 与 soul 配置目录（项目根目录下 system_prompt/）
PROMPT_DIR = os.path.join(_PROJECT_ROOT, "system_prompt")

# 长期记忆持久化文件（项目根目录下 data/memory.json），跨会话共享
MEMORY_FILE = os.path.join(_PROJECT_ROOT, "data", "memory.json")

# ===== 上下文压缩（compaction）配置 =====
# 触发策略：当前 session 的消息条数超过 COMPACT_MAX_MESSAGES，
# 或所有消息内容的总字符数超过 COMPACT_MAX_CHARS 时，触发一次压缩；
# 两个条件满足其一即触发，均未超过则不触发。阈值可通过 .env 覆盖。
COMPACT_MAX_MESSAGES = int(os.getenv("COMPACT_MAX_MESSAGES", "20"))
COMPACT_MAX_CHARS = int(os.getenv("COMPACT_MAX_CHARS", "4000"))
# 压缩时保留最近 N 条原始消息，更早的消息合并进 session.summary。
COMPACT_RECENT_MESSAGES = int(os.getenv("COMPACT_RECENT_MESSAGES", "8"))

# ===== Gateway（Step 6）配置 =====
# Gateway HTTP server 监听地址与端口，可通过 .env 覆盖。
GATEWAY_HOST = os.getenv("GATEWAY_HOST", "127.0.0.1")
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8000"))
# web 图形化入口静态资源目录（项目根目录下 web/）。
WEB_DIR = os.path.join(_PROJECT_ROOT, "web")
# 单个上传附件的大小上限（字节），默认 20MB。
ATTACHMENT_MAX_BYTES = int(os.getenv("ATTACHMENT_MAX_BYTES", str(20 * 1024 * 1024)))


def load_api_key() -> str:
    """从 .env 文件（环境变量 LLM_API_KEY）加载 API Key"""
    key = os.getenv("LLM_API_KEY", "").strip()
    if not key:
        raise ValueError(
            f"未能读取到 API Key。\n"
            f"请在 {_ENV_FILE} 中设置 LLM_API_KEY=<你的 API Key>。"
        )
    return key
