"""
DD-SJTUClaw Shell Tool 支撑（Step 8）。

提供一个可复用的 shell 会话：
- new_shell 启动一个新 shell（旧 shell 先退出），初始工作目录为 workspace 或其子目录；
- run_command 在同一个 shell 中执行命令，前序命令设置的 cwd / 环境变量会影响后续命令；
- shell 只能在 workspace 内执行命令：执行前后都校验 cwd 仍在 workspace 内，一旦离开则终止 shell。

Windows 实现说明：
本项目运行在 Windows / PowerShell 环境，但 shell 命令统一通过 cmd.exe 执行以便可靠地
捕获退出码、结束目录与环境变量。每次 run_command 用 cmd.exe /v:on 包裹用户命令，并在其后
追加带哨兵标记的 echo / set，从 stdout 中解析出退出码、结束 cwd 与环境快照，从而在多次
run_command 之间“持久化” cwd 与环境变量（相当于同一个长期 shell 会话的效果）。

仅使用 Python 标准库。
"""

import os
import subprocess
from pathlib import Path

from .workspace import is_within

# stdout 中用于分隔用户输出与运行期状态的哨兵标记（用户命令几乎不可能原样打印这些串）。
_RC = "___CLAW_RC___"
_CWD = "___CLAW_CWD___"
_ENV_BEGIN = "___CLAW_ENV_BEGIN___"
_ENV_END = "___CLAW_ENV_END___"


class ShellError(Exception):
    """shell 相关错误（无活动 shell / 离开 workspace / 命令为空等）。"""


class ShellSession:
    """一个绑定到 workspace 的 shell 会话，跨多次 run 持久化 cwd 与环境变量。"""

    def __init__(self, workspace, cwd=None, timeout=30.0, output_max_chars=20000):
        self.workspace = str(Path(workspace).resolve())
        self.cwd = str(Path(cwd).resolve()) if cwd else self.workspace
        self.env = dict(os.environ)
        self.timeout = float(timeout)
        self.output_max_chars = int(output_max_chars)
        self.closed = False

    def run(self, command):
        """在当前 shell 中执行一条命令，返回结构化结果 dict。"""
        if self.closed:
            raise ShellError("shell 已关闭，请先调用 new_shell 启动新的 shell。")
        if not command or not str(command).strip():
            raise ShellError("命令不能为空。")
        # 执行前校验：cwd 仍需在 workspace 内。
        if not is_within(self.workspace, self.cwd):
            self.closed = True
            raise ShellError(f"shell 当前目录已离开 workspace（{self.cwd}），已终止该 shell。")

        wrapped = (
            f"{command} & echo {_RC}!errorlevel! & echo {_CWD}!cd! "
            f"& echo {_ENV_BEGIN} & set & echo {_ENV_END}"
        )
        argv = ["cmd.exe", "/v:on", "/c", wrapped]

        timed_out = False
        try:
            proc = subprocess.run(
                argv, cwd=self.cwd, env=self.env,
                capture_output=True, timeout=self.timeout,
            )
            stdout_raw, stderr_raw = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout_raw = e.stdout or b""
            stderr_raw = e.stderr or b""
        except OSError as e:
            raise ShellError(f"无法启动 shell 进程: {e}")

        stdout = _decode(stdout_raw)
        stderr = _decode(stderr_raw)
        rc, new_cwd, new_env, user_stdout = _parse_stdout(stdout)

        if not timed_out:
            if new_cwd:
                self.cwd = new_cwd
            if new_env:
                self.env = new_env

        # 执行后校验：cwd 若离开 workspace，终止 shell 并在结果中标注。
        left = False
        if not timed_out and not is_within(self.workspace, self.cwd):
            left = True
            self.closed = True

        out_trunc = len(user_stdout) > self.output_max_chars
        err_trunc = len(stderr) > self.output_max_chars
        if out_trunc:
            user_stdout = user_stdout[:self.output_max_chars] + "\n...(stdout 已截断)"
        if err_trunc:
            stderr = stderr[:self.output_max_chars] + "\n...(stderr 已截断)"

        return {
            "command": command,
            "cwd": self.cwd,
            "returncode": (None if timed_out else (rc if rc is not None else -1)),
            "stdout": user_stdout,
            "stderr": stderr,
            "timedOut": timed_out,
            "truncated": out_trunc or err_trunc,
            "leftWorkspace": left,
        }


class ShellManager:
    """按 session 维护唯一的活动 shell；new_shell 会替换（关闭）旧 shell。"""

    def __init__(self, timeout=30.0, output_max_chars=20000):
        self.timeout = float(timeout)
        self.output_max_chars = int(output_max_chars)
        self._shells: dict[str, ShellSession] = {}

    def new_shell(self, session_id, workspace, cwd=None):
        old = self._shells.get(session_id)
        if old is not None:
            old.closed = True  # 旧 shell 先退出
        shell = ShellSession(workspace, cwd=cwd,
                             timeout=self.timeout, output_max_chars=self.output_max_chars)
        self._shells[session_id] = shell
        return shell

    def get_shell(self, session_id):
        shell = self._shells.get(session_id)
        if shell is None or shell.closed:
            return None
        return shell

    def close(self, session_id):
        shell = self._shells.pop(session_id, None)
        if shell is not None:
            shell.closed = True


def _decode(raw):
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", errors="replace")
    return raw or ""


def _parse_stdout(stdout):
    """从包裹后的 stdout 中解析出 (returncode, new_cwd, new_env, 用户真实 stdout)。"""
    rc = None
    new_cwd = None
    env = {}
    user_lines = []
    in_env = False
    for line in stdout.splitlines():
        if line.startswith(_RC):
            try:
                rc = int(line[len(_RC):].strip())
            except ValueError:
                rc = None
            continue
        if line.startswith(_CWD):
            new_cwd = line[len(_CWD):].strip() or None
            continue
        if line.startswith(_ENV_BEGIN):
            in_env = True
            continue
        if line.startswith(_ENV_END):
            in_env = False
            continue
        if in_env:
            if "=" in line:
                k, v = line.split("=", 1)
                # 跳过 cmd 的每盘符特殊变量（形如 "=C:"）与空键。
                if k and not k.startswith("="):
                    env[k] = v
            continue
        user_lines.append(line)
    return rc, new_cwd, (env or None), "\n".join(user_lines)
