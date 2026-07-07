"""
DD-SJTUClaw Skill System（Step 9）。

Skill 是面向某类任务的可复用能力包（工作方法、模板、检查清单、参考资料）。
它与其它上下文的边界：
- system prompt：claw 的通用行为规则；
- memory：跨 session 的长期事实与偏好；
- skill：某类任务的可复用工作方法（只在任务需要时才进入上下文）；
- session messages：当前对话的具体过程。

本模块只负责：扫描本地 skills/ 目录、维护可用 skill 列表、生成轻量索引供模型判断、
按名称加载某个 skill 的完整内容（SKILL.md 正文 + 资源文件）。
它不执行任务——真正的任务执行仍由已有 agent loop 完成。

SKILL.md 使用简单的 frontmatter：
    ---
    name: example-skill
    description: 说明这个 skill 能处理什么任务，以及什么时候该用它。
    ---
    # 正文（instructions）...

仅使用 Python 标准库。
"""

from pathlib import Path

# 单个资源文件加入上下文时的字符上限，避免把过大的文件整篇塞进 LLM 请求。
SKILL_RESOURCE_MAX_CHARS = 8000


class SkillError(Exception):
    """skill 相关错误（skill 不存在、加载失败等）。"""


def parse_frontmatter(text):
    """解析 SKILL.md 顶部由 --- 包围的 frontmatter（简单的 key: value 行）。

    返回 (meta: dict, body: str)。没有合法 frontmatter 时 meta 为空、body 为全文。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    meta = {}
    body_start = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body_start = i + 1
            break
        line = lines[i]
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            if key:
                meta[key] = val.strip()
    if body_start is None:
        # frontmatter 未闭合，视为无 frontmatter
        return {}, text.strip()
    body = "\n".join(lines[body_start:]).strip()
    return meta, body


class Skill:
    """一个 skill：元信息（name/description）+ 指令正文（instructions）+ 资源文件。

    资源在 load() 时才真正读入 self.resources（rel_path -> content）；
    仅列目录 / 索引时不加载正文与资源之外的内容。
    """

    def __init__(self, name, description, instructions, directory, resources=None):
        self.name = name
        self.description = description
        self.instructions = instructions
        self.directory = Path(directory)
        self.resources = resources if resources is not None else {}

    def index_entry(self):
        """轻量索引项：只含 name 与 description。"""
        return {"name": self.name, "description": self.description}

    def render_full(self):
        """渲染成加入 agent loop 的完整 skill 文本（含指令与资源）。"""
        parts = [
            f"【已加载 skill：{self.name}】",
            f"描述：{self.description}",
            "",
            "使用说明与工作方法：",
            self.instructions or "（无正文）",
        ]
        for rel, content in self.resources.items():
            parts.append("")
            parts.append(f"--- 资源文件：{rel} ---")
            parts.append(content)
        return "\n".join(parts)


class SkillRegistry:
    """扫描并管理本地 skills/ 目录中的可用 skill。"""

    def __init__(self, skills_dir):
        self.skills_dir = Path(skills_dir)
        self.skills = {}  # name -> Skill（元信息 + instructions，resources 延迟加载）
        self._scan()

    def _scan(self):
        """扫描 skills 目录：每个含 SKILL.md 的子目录视为一个 skill。
        单个 skill 解析失败时跳过，不影响其它 skill。"""
        if not self.skills_dir.exists() or not self.skills_dir.is_dir():
            return
        for sub in sorted(self.skills_dir.iterdir()):
            if not sub.is_dir():
                continue
            skill_md = sub / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                text = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta, body = parse_frontmatter(text)
            name = (meta.get("name") or sub.name).strip()
            description = meta.get("description", "").strip()
            if not name:
                continue
            self.skills[name] = Skill(name, description, body, sub)

    # ---------- 查询 ----------
    def all(self):
        """按名称排序返回全部 Skill 对象。"""
        return [self.skills[k] for k in sorted(self.skills)]

    def list(self):
        """列出全部 skill 的轻量信息（name + description），供 CLI / web 展示。"""
        return [s.index_entry() for s in self.all()]

    def has(self, name):
        return name in self.skills

    def get(self, name):
        return self.skills.get(name)

    def index_text(self):
        """生成一份轻量 skill 索引文本（只含 name + description），供模型判断是否使用 skill。"""
        if not self.skills:
            return ""
        lines = []
        for s in self.all():
            lines.append(f"- {s.name}: {s.description}")
        return "\n".join(lines)

    # ---------- 加载完整内容 ----------
    def load(self, name):
        """加载指定 skill 的完整内容：读取 SKILL.md 之外的所有资源文件到 resources，
        返回该 Skill（resources 已填充）。skill 不存在时抛 SkillError。"""
        skill = self.skills.get(name)
        if skill is None:
            raise SkillError(f"不存在名为 '{name}' 的 skill")
        resources = {}
        try:
            files = sorted(p for p in skill.directory.rglob("*") if p.is_file())
        except OSError as e:
            raise SkillError(f"读取 skill 目录失败: {e}")
        for path in files:
            if path.name == "SKILL.md":
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(content) > SKILL_RESOURCE_MAX_CHARS:
                content = content[:SKILL_RESOURCE_MAX_CHARS] + "\n...（资源过大，已截断）"
            rel = path.relative_to(skill.directory).as_posix()
            resources[rel] = content
        skill.resources = resources
        return skill


def build_skills_prompt(registry, active_skills):
    """组装注入 agent loop 的 skill 系统提示段。

    - 已选中（active）的 skill：注入其完整内容（指令 + 资源），指导模型完成任务；
    - 其余可用 skill：只给轻量索引（name + description），并说明如何请求使用；
      模型自主请求使用某个 skill 需经用户确认（approval）。
    active_skills 为已加载完整内容的 Skill 列表。"""
    if registry is None:
        return ""
    active_names = {s.name for s in active_skills}
    lines = []

    for s in active_skills:
        lines.append(s.render_full())
        lines.append("")

    others = [s for s in registry.all() if s.name not in active_names]
    if others:
        if active_skills:
            lines.append("此外还有以下可用 skill（仅当任务确实匹配时才考虑）：")
        else:
            lines.append(
                "你可以使用以下 skill（面向某类任务的可复用工作方法）。"
                "仅当用户任务明确匹配某个 skill 时才使用："
            )
        for s in others:
            lines.append(f"- {s.name}: {s.description}")
        lines.append("")
        lines.append(
            "若某个 skill 适合当前任务，请回复（且仅回复）："
            '{"type":"use_skill","skill":"<skill 名称>","reason":"<为什么适合这个任务>"}。'
            "使用 skill 前会请用户确认；确认后你会在后续轮次看到该 skill 的完整说明与模板。"
            "若任务并不需要 skill，则正常调用 tool 或直接输出 final。"
        )

    return "\n".join(lines).strip()
