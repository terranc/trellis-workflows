# trellis-workflows

Trellis workflow 模板市场。当前包含：

- `native-ai` — 只用 Trellis 对齐 PRD 和 design，代码由主会话直接实现；
  不创建 `implement.md`，不派发 implement/check 子代理。

模板内置 **task hold** 约定：用 `task.json.meta.hold`（`blocked` / `paused`）
标记挂起，`status` 保持不变，看板读取同一字段。命令见模板的 `### Task Hold`。

## 完整安装（新项目）

公开仓库用 `gh:` source。注意：`--workflow-source` 只是查找源，必须同时写
`--workflow native-ai`；只写 source 仍会安装默认的 `native` workflow。

```bash
# 1. 初始化 Trellis，并选用 native-ai workflow
trellis init --claude -y \
  --workflow native-ai \
  --workflow-source gh:terranc/trellis-workflows

# 2. 写入技能和 continue 命令
git clone https://github.com/terranc/trellis-workflows.git /tmp/trellis-workflows
python3 /tmp/trellis-workflows/install-native-ai.py --root .
```

私有 fork 或需要 SSH 时，把 source 换成：

```bash
--workflow-source git@github.com:terranc/trellis-workflows.git
git clone git@github.com:terranc/trellis-workflows.git /tmp/trellis-workflows
```

第 2 步会更新：

- `trellis-brainstorm` 技能（PRD + design，不创建 implement.md）
- `trellis-start` / `trellis-continue` 技能
- `continue` 命令（claude / cursor / opencode）

安装器只更新技能和 continue 命令。Native AI 行为由 `.trellis/workflow.md`
和这些技能承担。

写入前会备份到 `.trellis/.backup-native-ai-<时间戳>/`，可重复执行。

## 已有项目切换

```bash
trellis workflow -t native-ai \
  -m gh:terranc/trellis-workflows -f

git clone https://github.com/terranc/trellis-workflows.git /tmp/trellis-workflows
python3 /tmp/trellis-workflows/install-native-ai.py --root .
```

## 结构

```
index.json
workflows/native-ai/workflow.md     # workflow 模板（trellis init 读取）
overlay/                            # 技能和命令
scripts/validate.py                 # index + workflow 契约校验
install-native-ai.py                # 把 overlay 写入目标项目
```

## 维护

新增模板：

1. 创建 `workflows/<id>/workflow.md`
2. 在 `index.json` 加一条 `type: "workflow"` 的条目
3. 运行 `python3 scripts/validate.py`

约定：

- `id` 发布后不要改，项目会用它做 `--workflow`
- `path` 必须在仓库内、以 `.md` 结尾
- `[workflow-state:STATUS]` 开闭标签必须成对
- 至少保留 `no_task`、`planning`、`in_progress` 三个核心状态
- 修改后可以本地验证：
  ```bash
  trellis workflow --list -m gh:terranc/trellis-workflows
  ```

## 校验

```bash
python3 scripts/validate.py
```
