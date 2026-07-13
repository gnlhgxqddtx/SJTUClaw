# SJTUClaw

SJTUClaw 是一个简化且面向教学的 claw agent（爪子智能体）。用户通过命令行（CLI）或内置的 Web 图形化入口与它对话；agent 负责维护会话、组装上下文、调用 LLM，并在需要时读取环境、调用工具执行任务、保存结果。

项目仅依赖极少的第三方库（`openai` 与 `python-dotenv`），其余能力（HTTP 服务、Web 前端、定时任务、工具系统等）全部基于 Python 标准库实现，便于逐层阅读与教学演示。

> 当前版本：**v1.0.0（稳定版）**，已完成课程需求 Step 1–9 的全部功能。

---

## ✨ 功能特性

- **多轮对话与多会话管理**：支持创建、切换、重命名、删除会话；每个会话独立持久化为 JSON，重启后自动恢复。
- **稳定上下文分离**：system prompt、soul（人设）、长期记忆作为「稳定上下文」与会话历史分离，每次请求都重新组装为单条 system 消息置于最前，不会被普通对话覆盖。
- **长期记忆**：跨会话共享的长期记忆，支持增删查，自动注入每次请求的上下文。
- **上下文压缩（compaction）**：会话消息超过阈值（条数或总字符数）时，自动把较早消息压缩为摘要并保留最近若干条；也可通过 `/compact` 手动触发。压缩失败不会删除原始消息。
- **工具系统与 Agent Loop**：统一的 Tool 数据结构与注册表，模型通过 JSON 文本协议发起工具调用，形成「LLM → 工具 → LLM」的循环；工具调用轨迹在 CLI 与 Web 中可见。
- **只读工具（免审批）**：`current_time`、`list_dir`、`read_file`。
- **高级工具与人工审批**：写文件 / 执行命令 / 下载类工具（`create_file`、`overwrite_file`、`edit_file`、`copy_attachment_to_workspace`、`new_shell`、`run_command`、`create_download`）在执行前需用户审批，且必须先设置 workspace，超时按拒绝处理。
- **Workspace 隔离**：为会话设置可操作的项目目录，高级工具的读写被限制在 workspace 内。
- **Gateway HTTP 服务 + Web 图形化入口**：基于标准库 `http.server` 的长驻服务，原生 JS + fetch 前端，支持会话管理、附件上传（按会话隔离）、工具轨迹展示与审批交互。
- **定时任务 Scheduler**：一次性 / 周期性任务，持久化到本地并在重启后恢复，到期自动进入 agent loop 执行。
- **技能系统（Skill System）**：本地 `skills/` 目录下每个含 `SKILL.md` 的子目录即一个技能；支持用户显式调用（`/skill <name> <task>`，免审批）与模型自主调用（需审批），并记录技能使用轨迹。

---

## 🧱 技术栈

- **语言**：Python 3.10+
- **LLM 调用**：`openai`（OpenAI 兼容协议），默认对接 SJTU 模型服务
- **配置**：`python-dotenv`，密钥与参数从项目根目录 `.env` 读取
- **服务与前端**：Python 标准库 `http.server` + 原生 HTML / JavaScript（`fetch`）
- **持久化**：本地 JSON 文件（会话、长期记忆、定时任务）

---

## 📁 目录结构

```
SJTUClaw/
├── source/                     # 源码
│   ├── main.py                 # CLI 交互式入口（python -m source.main）
│   ├── gateway.py              # HTTP 服务 + Web 入口（python -m source.gateway）
│   ├── agent.py                # AgentRuntime：CLI/Gateway/Scheduler 共用的统一执行路径
│   ├── config.py               # 配置（从 .env 读取，含默认值）
│   ├── llm_client.py           # LLM 客户端（OpenAI 兼容，支持流式）
│   ├── session_manager.py      # 会话数据模型与多会话管理 + JSON 持久化
│   ├── context_builder.py      # 稳定上下文 + 会话历史的消息组装
│   ├── prompt_loader.py        # system prompt / soul 加载
│   ├── memory_store.py         # 长期记忆存储
│   ├── compaction.py           # 上下文压缩
│   ├── workspace.py            # workspace 规范化与校验
│   ├── approval.py             # 人工审批
│   ├── attachments.py          # 附件存储（按会话隔离）
│   ├── downloads.py            # 下载入口注册表
│   ├── shell.py                # 命令执行封装
│   ├── scheduler.py            # 定时任务调度与持久化
│   ├── skills.py               # SkillRegistry：技能扫描 / 索引 / 加载
│   └── tools/                  # 工具系统
│       ├── base.py             # Tool / ToolRegistry / ToolContext / 安全级别
│       ├── readonly.py         # 只读工具
│       ├── advanced.py         # 高级工具（需审批）
│       └── protocol.py         # 工具调用 JSON 协议解析与提示词
├── skills/                     # 本地技能（每个子目录含 SKILL.md）
│   ├── course-report/
│   ├── material-summary/
│   └── presentation-outline/
├── web/
│   └── index.html              # Web 图形化入口
├── system_prompt/              # 提示词配置（真实文件本地个性化，仅提交 .example 模板）
│   ├── system_prompt.example.md
│   └── SOUL.example.md
├── data/                       # 运行时数据（会话 / 记忆 / 定时任务，均本地生成不入库）
│   └── sessions/.gitkeep
├── .env.example                # 环境变量模板
├── requirements.txt
└── readme.md
```

---

## 🚀 快速开始

### 1. 环境要求

- Python 3.10 或更高版本
- 一个可用的 LLM API Key（默认对接 `https://models.sjtu.edu.cn/api/v1`）

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 API Key

复制环境变量模板并填入真实值（`.env` 已被 `.gitignore` 忽略，不会入库）：

```bash
cp .env.example .env
```

编辑 `.env`：

```ini
LLM_API_KEY=你的真实API Key
LLM_BASE_URL=https://models.sjtu.edu.cn/api/v1
LLM_MODEL=deepseek-chat
```

### 4. 配置提示词（可选）

`system_prompt/` 下的真实提示词文件为本地个性化内容、不入库，仓库仅提供 `.example` 模板。首次使用可复制模板：

```bash
cp system_prompt/system_prompt.example.md system_prompt/system_prompt.md
cp system_prompt/SOUL.example.md system_prompt/SOUL.md
```

### 5. 启动命令行（CLI）

```bash
python -m source.main
```

### 6. 启动 Web 图形化入口

```bash
python -m source.gateway
```

启动后在浏览器打开 `http://127.0.0.1:8000`（可通过 `.env` 的 `GATEWAY_HOST` / `GATEWAY_PORT` 调整）。Gateway 会同时启动定时任务调度器。

---

## 💬 CLI 命令

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示帮助 |
| `/exit` | 退出程序 |
| `/session list` | 列出所有会话（`*` 为当前会话） |
| `/session new` | 创建并切换到新会话 |
| `/session switch <id>` | 切换会话（支持 `001` / `1` 等简写） |
| `/session delete <id>` | 删除指定会话 |
| `/session rename <id> <title>` | 重命名会话 |
| `/memory list` | 列出长期记忆 |
| `/memory add <内容>` | 添加长期记忆 |
| `/memory delete <id>` | 删除长期记忆 |
| `/workspace show` | 查看当前会话的 workspace |
| `/workspace set <path>` | 设置 workspace 目录 |
| `/skill list` | 列出全部可用技能 |
| `/skill show <name>` | 查看某个技能的完整说明与资源 |
| `/skill usage` | 查看当前会话的技能使用记录 |
| `/skill <name> <task>` | 用指定技能完成任务（免审批） |
| `/compact` | 立即压缩当前会话的较早消息 |

直接输入文本即为与模型对话；模型可在对话中自主发起工具调用，高级工具执行前会在 CLI 请求 `y/N` 审批。

---

## 🌐 HTTP 接口

| 方法与路径 | 说明 |
| --- | --- |
| `GET /` | Web 图形化入口 |
| `GET /api/health` | 健康检查 |
| `GET /api/sessions` | 列出所有会话 |
| `POST /api/sessions` | 新建会话 `{title?}` |
| `GET /api/sessions/<id>/messages` | 会话消息历史 |
| `POST /api/chat` | 走 agent loop，返回回复与事件 `{sessionId?, message, skill?}` |
| `GET /api/sessions/<id>/attachments` | 会话附件列表（按会话隔离） |
| `POST /api/sessions/<id>/attachments` | 上传附件 `{filename, type?, dataBase64}` |
| `GET /api/skills` | 列出全部技能（轻量索引） |
| `GET /api/skills/<name>` | 某技能完整说明与资源文件名 |
| `GET /api/sessions/<id>/skill-usage` | 会话内技能使用记录 |

> 附件通过 base64 承载；带 `sessionId` 但不存在时返回 404（不隐式新建，避免拼写错误产生垃圾会话）。

---

## 🛠️ 工具与审批

- **只读工具**（安全，免审批）：`current_time`、`list_dir`、`read_file`。
- **高级工具**（需审批 + 需 workspace）：`create_file`、`overwrite_file`、`edit_file`、`copy_attachment_to_workspace`、`new_shell`、`run_command`、`create_download`。

模型以 JSON 文本协议发起工具调用，运行时解析并执行，再把结果作为 observation 回传给模型继续推理，形成 agent loop。高级工具执行前需人工审批（CLI 为 `y/N` 阻塞询问，Web 为审批弹窗），审批超时按拒绝处理。

---

## 🧩 技能（Skills）

`skills/` 下每个包含 `SKILL.md` 的子目录即一个技能，`SKILL.md` 由 frontmatter（`name` / `description`）与正文（操作说明）组成，可附带 `assets/`、`references/` 等资源。内置示例技能：

- `course-report`：课程报告撰写
- `material-summary`：资料摘要
- `presentation-outline`：演示大纲

调用方式：

- **显式调用**：`/skill <name> <task>`，直接加载技能完整内容，免审批。
- **模型自主调用**：模型判断需要某技能时发起调用，需用户审批后才加载。

技能使用会记录到会话的 `skillUsages` 中，可通过 `/skill usage` 或 `GET /api/sessions/<id>/skill-usage` 查看。

---

## ⚙️ 配置项（.env）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` | （必填） | LLM API Key |
| `LLM_BASE_URL` | `https://models.sjtu.edu.cn/api/v1` | API 基础地址 |
| `LLM_MODEL` | `glm` | 默认模型（`deepseek-chat` / `deepseek-reasoner` / `minimax` / `glm` / `qwen`） |
| `COMPACT_MAX_MESSAGES` | `20` | 触发压缩的消息条数阈值 |
| `COMPACT_MAX_CHARS` | `4000` | 触发压缩的总字符数阈值 |
| `COMPACT_RECENT_MESSAGES` | `8` | 压缩时保留的最近消息条数 |
| `GATEWAY_HOST` | `127.0.0.1` | Gateway 监听地址 |
| `GATEWAY_PORT` | `8000` | Gateway 监听端口 |
| `ATTACHMENT_MAX_BYTES` | `20971520` | 单个附件大小上限（字节，默认 20MB） |
| `SCHEDULER_POLL_SECONDS` | `5` | 定时任务轮询间隔（秒） |
| `SHELL_TIMEOUT_SECONDS` | `30` | 命令执行超时（秒） |
| `SHELL_OUTPUT_MAX_CHARS` | `20000` | 命令输出展示上限（字符） |
| `APPROVAL_TIMEOUT_SECONDS` | `300` | 审批等待超时（秒，超时按拒绝） |

---

## 🗂️ 数据与持久化

- 会话：`data/sessions/<session_id>.json`
- 长期记忆：`data/memory.json`
- 定时任务：`data/scheduler/tasks.json`

以上运行时数据均为本地生成，已在 `.gitignore` 中忽略，不纳入版本库。

---

## 🌿 分支模型

项目遵循 Git Flow：

- `main`：稳定发布分支，仅合入发布，并打版本标签（`v1.0.0` 等）。
- `develop`：日常开发分支。
- `release-x.y.z`：发布分支，从 `develop` 切出，合入 `main` 后再合回 `develop`。

---

## 📄 许可证

暂未指定。

---

## 联系方式与致谢

- 邮箱：gnlhgxqddtx@sjtu.edu.cn
- 项目主页：https://github.com/gnlhgxqddtx/SJTUClaw
- 感谢所有 Contributors 的贡献。
- 如果本项目对你有帮助，请给一个 ⭐️ 支持我们！
