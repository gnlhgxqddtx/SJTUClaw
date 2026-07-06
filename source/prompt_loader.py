"""
DD-SJTUClaw 提示词加载模块
从 system_prompt/ 目录读取 Markdown 配置文件，组装稳定的系统提示与人格（soul）。
将 system prompt 与 soul 从代码中拆出，改为从配置文件加载。
仅使用 Python 标准库。
"""

from pathlib import Path


class PromptLoader:
    """从 system_prompt/ 目录加载 system prompt 与 soul 配置。"""

    SYSTEM_PROMPT_FILE = "system_prompt.md"
    SOUL_FILE = "SOUL.md"

    def __init__(self, prompt_dir):
        self.prompt_dir = Path(prompt_dir)

    def _read(self, filename):
        path = self.prompt_dir / filename
        if not path.exists():
            print(f"[警告] 未找到提示词配置文件: {path}")
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"[警告] 读取提示词配置失败（{path}）: {e}")
            return ""

    def system_prompt(self):
        return self._read(self.SYSTEM_PROMPT_FILE)

    def soul(self):
        return self._read(self.SOUL_FILE)

    def stable_prompt(self):
        """把 system prompt 与 soul 合并为一段稳定的系统提示文本。"""
        parts = [self.system_prompt(), self.soul()]
        return "\n\n".join(p for p in parts if p)
