# trellis-workflows

Trellis workflow 模板市场。当前包含：

- `native-ai` — 只用 Trellis 对齐 PRD 和 design，代码由主会话直接实现；
  不创建 `implement.md`，不派发 implement/check 子代理。

## 完整安装（新项目）

私有仓库（当前）用 SSH source：

```bash
# 1. 初始化 Trellis，并选用 native-ai workflow
trellis init --claude -y \
  --workflow native-ai \
  --workflow-source git@github.com:terranc/trellis-workflows.git

# 2. 写入技能、AGENTS.md 约定和 merge 脚本
git clone git@github.com:terranc/trellis-workflows.git /tmp/trellis-workflows
python3 /tmp/trellis-workflows/install-native-ai.py --root .
```

第 2 步会更新：

- `trellis-brainstorm` 技能（PRD + design，不创建 implement.md）
- `trellis-start` / `trellis-continue` 技能
- `continue` 命令（claude / cursor / opencode）
- 根目录 `AGENTS.md` 的 `## Native AI Mode` 段落
- `scripts/trellis-merge-new.py`

写入前会备份到 `.trellis/.backup-native-ai-<时间戳>/`，可重复执行。

## 已有项目切换

```bash
trellis workflow -t native-ai \
  -m git@github.com:terranc/trellis-workflows.git -f

git clone git@github.com:terranc/trellis-workflows.git /tmp/trellis-workflows
python3 /tmp/trellis-workflows/install-native-ai.py --root .
```

如果以后改成公开仓库，可以把 source 换成：

```bash
--workflow-source gh:terranc/trellis-workflows
```

## 结构

```
index.json
workflows/native-ai/workflow.md     # workflow 模板（trellis init 读取）
overlay/                            # 技能、命令、AGENTS 片段
scripts/trellis-merge-new.py        # 上游 .new 合并脚本
scripts/validate.py                 # index + workflow 契约校验
install-native-ai.py                # 把 overlay 写入目标项目
AGENTS.md                           # 维护约定
```

## 校验

```bash
python3 scripts/validate.py
```

维护约定见 `AGENTS.md`。
