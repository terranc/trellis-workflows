# Trellis Workflows

这个仓库是 Trellis 的 **workflow 模板市场**（marketplace），不是业务项目。
Trellis 通过这里的 `index.json` 发现 workflow，并在 `trellis init` 或
`trellis workflow` 时下载 `workflows/<id>/workflow.md`。

## 目录结构

```
index.json                         # marketplace 索引，Trellis 从这里发现模板
workflows/<id>/workflow.md         # 一个 workflow 一个文件
overlay/                           # Native AI 模式配套的技能/命令/AGENTS 片段
scripts/trellis-merge-new.py       # 上游 .new 合并脚本
scripts/validate.py                # 本地校验 index + workflow 契约
install-native-ai.py               # 把 overlay 写入目标项目
README.md                          # 给使用者看的快速说明
AGENTS.md                          # 给维护者/AI 看的开发约定（本文件）
```

## Trellis 如何消费

当前仓库是 **public**，使用 `gh:` source：

```bash
trellis workflow --list -m gh:terranc/trellis-workflows
trellis init --workflow native-ai \
  --workflow-source gh:terranc/trellis-workflows -y --claude
```

如果以后改成 private 或需要 SSH，可以用：

```bash
trellis workflow --list -m git@github.com:terranc/trellis-workflows.git
trellis init --workflow native-ai \
  --workflow-source git@github.com:terranc/trellis-workflows.git \
  -y --claude
```

`trellis init --workflow` 只写 `.trellis/workflow.md`。Native AI 模式还依赖
平台 skill / command 的文案；这些不在 workflow 模板里，需要在目标项目里
单独同步（见下文“模板边界”）。

## 当前模板：native-ai

`workflows/native-ai/workflow.md` 定义：

- Phase 1 只产出 `prd.md` + `design.md`
- 不创建 `implement.md`、`implement.jsonl`、`check.jsonl`
- Phase 2 由主会话直接实现，不派发 implement/check 子代理
- 验证使用项目自己的 build / lint / test 命令
- `task.py start` 使用 `--allow-empty-context`

## 修改流程

1. **从真实项目同步**（推荐）：
   ```bash
   cp /path/to/project/.trellis/workflow.md \
      workflows/native-ai/workflow.md
   ```
   或者直接编辑 `workflows/native-ai/workflow.md`。
2. **校验**：
   ```bash
   python3 scripts/validate.py
   ```
3. **在临时项目里真实验证**（推送到 GitHub 之后）：
   ```bash
   tmp=$(mktemp -d) && cd "$tmp" && git init
   trellis init --claude -y \
     --workflow native-ai \
     --workflow-source gh:terranc/trellis-workflows
   head -40 .trellis/workflow.md
   trellis workflow --list -m gh:terranc/trellis-workflows
   ```
4. **提交与推送**：
   ```bash
   git add -A
   git commit -m "feat(native-ai): ..."
   git push origin main
   ```
   行为变化建议打 tag，例如 `v0.2.2`。公开仓库可以用
   `gh:terranc/trellis-workflows#v0.2.2` 锁定版本。

## workflow.md 契约

Trellis 的 hook 只解析 `workflow.md`，不保存 fallback 文案，所以：

- `[workflow-state:STATUS]` 与 `[/workflow-state:STATUS]` 必须成对，STATUS 一致。
- STATUS 字符集：`[A-Za-z0-9_-]+`。
- 每个 `[required · once]` 步骤都要在对应 phase 的 breadcrumb block 里有体现；
  否则 AI 会在那一轮静默跳过该步骤。
- 平台路由标记（`[Claude Code, Cursor, ...]` / `[/...]`）是脚本消费的，
  不要改语法或随意删除。
- 这个文件是单文件模板：resolver 要求 `path` 以 `.md` 结尾，不能指向目录。
- 不要放入项目专用路径、内部域名、密钥、个人环境变量。

## index.json 契约

每个条目至少包含：

```json
{
  "id": "native-ai",
  "type": "workflow",
  "name": "Native AI Workflow",
  "description": "一句话说明",
  "path": "workflows/native-ai/workflow.md",
  "tags": ["workflow", "native-ai"]
}
```

规则：

- `id` 是稳定标识，发布后不要改名（项目会用它做 `--workflow`）。
- `type` 必须是 `workflow`。
- `path` 必须位于仓库内、以 `.md` 结尾、不能包含 `..`。
- 新增模板时同时添加目录、`index.json` 条目，并跑校验。

## 模板边界

workflow 模板只覆盖 `.trellis/workflow.md`。`trellis init --workflow` 不会
安装 overlay 里的技能、命令或 `AGENTS.md` 片段，所以完整安装是两步：

```bash
# 1. workflow
trellis init --workflow native-ai \
  --workflow-source git@github.com:terranc/trellis-workflows.git -y --claude

# 2. 配套文件
git clone git@github.com:terranc/trellis-workflows.git /tmp/trellis-workflows
python3 /tmp/trellis-workflows/install-native-ai.py --root .
```

`overlay/` 当前包含：

- `trellis-brainstorm` 技能（PRD + design，不创建 implement.md）
- `trellis-start` / `trellis-continue` 技能路由
- `continue` 命令（claude / cursor / opencode）
- 根目录 `AGENTS.md` 的 Native AI 约定
- `scripts/trellis-merge-new.py`（合并上游 `.new`）

安装器只更新目标项目里已经存在的技能/命令文件；不存在的平台目录会跳过。
写入前备份到 `.trellis/.backup-native-ai-<时间戳>/`，内容相同则跳过，可重复执行。

## 运行时契约

参考 Trellis 的“定制 Workflow”文档，以下约定被脚本消费：

- Phase 标题层级 `## Phase X` + `#### X.Y` 被
  `get_context.py --mode phase --step X.Y` 解析。不要改成 `###`，也不要改
  `X.Y` 的编号格式。
- `[workflow-state:STATUS]` 块由 `inject-workflow-state.py` 每轮解析；保持
  简短（≈200 字节以内），不要写长段落。
- 自定义状态只需要一个 `[workflow-state:<name>]` 块，并把
  `task.json.status` 设成同名值；不需要预注册。
- 修改生效时间：breadcrumb 文本下一条用户消息生效；Phase/step 正文和
  Skill Routing 下一个会话生效。
- 如果模板包含 `### Skill Routing` 或 `### DO NOT skip skills`，保持它们与
  Phase 内容一致；SessionStart 会把两者一起内联给 AI。

## 校验脚本做什么

`python3 scripts/validate.py` 会检查：

- `index.json` 可解析，`version` 和 `templates` 存在
- 每个 workflow 条目的 `id` / `type` / `name` / `path`
- `path` 存在、在仓库内、以 `.md` 结尾
- `[workflow-state:*]` 开闭标签成对
- 至少存在 `no_task`、`planning`、`in_progress` 三个核心状态

校验失败时退出码非 0，可以直接用在 CI 或 pre-commit。
