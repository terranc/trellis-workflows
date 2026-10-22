# Trellis Workflows

这个仓库是 Trellis 的 **workflow 模板市场**（marketplace），不是业务项目。
Trellis 通过这里的 `index.json` 发现 workflow，并在 `trellis init` 或
`trellis workflow` 时下载 `workflows/<id>/workflow.md`。

## 目录结构

```
index.json                         # marketplace 索引，Trellis 从这里发现模板
workflows/<id>/workflow.md         # 一个 workflow 一个文件
scripts/validate.py                # 本地校验 index + workflow 契约
README.md                          # 给使用者看的快速说明
AGENTS.md                          # 给维护者/AI 看的开发约定（本文件）
```

## Trellis 如何消费

公开仓库：

```bash
trellis workflow --list -m gh:terranc/trellis-workflows
trellis init --workflow native-ai --workflow-source gh:terranc/trellis-workflows -y
```

当前仓库是 **private**，`gh:` 的 raw HTTP 读取不可用，请用 SSH source：

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
   cp ~/www/youxiang/.trellis/workflow.md \
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
     --workflow-source git@github.com:terranc/trellis-workflows.git
   head -40 .trellis/workflow.md
   trellis workflow --list -m git@github.com:terranc/trellis-workflows.git
   ```
4. **提交与推送**：
   ```bash
   git add -A
   git commit -m "feat(native-ai): ..."
   git push origin main
   ```
   行为变化建议打 tag，例如 `v0.2.0`，方便项目锁定版本
   （`#v0.2.0` 可加在 source 后面）。

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

workflow 模板只覆盖 `.trellis/workflow.md`。Native AI 模式还包括：

- `trellis-brainstorm` 技能（PRD + design，不创建 implement.md）
- `trellis-start` / `trellis-continue` 的路由文案
- 根目录 `AGENTS.md` 的 Native AI 约定
- 可选的 `scripts/trellis-merge-new.py`（合并上游 `.new`）

这些需要在目标项目里同步。可以先把它们放在本仓库的 `overlay/` 下，再由
安装脚本写入目标项目；不要假装 `trellis init` 会自动完成这些。

## 校验脚本做什么

`python3 scripts/validate.py` 会检查：

- `index.json` 可解析，`version` 和 `templates` 存在
- 每个 workflow 条目的 `id` / `type` / `name` / `path`
- `path` 存在、在仓库内、以 `.md` 结尾
- `[workflow-state:*]` 开闭标签成对
- 至少存在 `no_task`、`planning`、`in_progress` 三个核心状态

校验失败时退出码非 0，可以直接用在 CI 或 pre-commit。
