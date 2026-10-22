# trellis-workflows

Trellis workflow 模板市场。当前包含：

- `native-ai` — 只用 Trellis 对齐 PRD 和 design，代码由主会话直接实现；
  不创建 `implement.md`，不派发 implement/check 子代理。

## 使用

私有仓库（当前）用 SSH source：

```bash
trellis workflow --list -m git@github.com:terranc/trellis-workflows.git

trellis init --workflow native-ai \
  --workflow-source git@github.com:terranc/trellis-workflows.git \
  -y --claude
```

如果以后改成公开仓库，可以用：

```bash
trellis init --workflow native-ai \
  --workflow-source gh:terranc/trellis-workflows \
  -y --claude
```

`trellis init --workflow` 只写 `.trellis/workflow.md`。Native AI 模式还依赖
brainstorm/start/continue 技能与 `AGENTS.md` 文案，需要按项目实际情况同步。

## 结构

```
index.json
workflows/native-ai/workflow.md
scripts/validate.py
AGENTS.md
```

## 校验

```bash
python3 scripts/validate.py
```

维护约定见 `AGENTS.md`。
