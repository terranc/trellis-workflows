#!/usr/bin/env python3
"""把 Native AI workflow 的配套文件写入一个已初始化的 Trellis 项目。

`trellis init --workflow native-ai` 只负责 `.trellis/workflow.md`。
这个脚本负责其余部分：

    - 更新 trellis-brainstorm / trellis-start / trellis-continue 技能
    - 更新 continue 命令（claude / cursor / opencode）
    - 在根目录 AGENTS.md 追加 Native AI Mode 约定
    - 复制 scripts/trellis-merge-new.py

用法：

    python3 install-native-ai.py --root /path/to/project
    python3 install-native-ai.py --root /path/to/project --dry-run

写入前会备份到 `.trellis/.backup-native-ai-<时间戳>/`。
已有相同内容的文件会跳过，可以重复执行。
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
OVERLAY = REPO_ROOT / "overlay"

PLATFORM_ROOTS = (
    ".claude",
    ".codex",
    ".cursor",
    ".grok",
    ".opencode",
    ".pi",
    ".agents",
    ".skillshare",
)

BRAINSTORM_TARGETS = tuple(
    f"{root}/skills/trellis-brainstorm/SKILL.md" for root in PLATFORM_ROOTS
)
START_TARGETS = (
    ".agents/skills/trellis-start/SKILL.md",
    ".kimi-code/skills/trellis-start/SKILL.md",
)
CONTINUE_TARGETS = (
    ".agents/skills/trellis-continue/SKILL.md",
    ".kimi-code/skills/trellis-continue/SKILL.md",
)
COMMAND_TARGETS = (
    ("overlay/commands/claude-continue.md", ".claude/commands/trellis/continue.md"),
    ("overlay/commands/cursor-continue.md", ".cursor/commands/trellis-continue.md"),
    ("overlay/commands/opencode-continue.md", ".opencode/commands/trellis/continue.md"),
)


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def backup(path: Path, root: Path, backup_dir: Path) -> Path | None:
    if not path.exists():
        return None
    target = backup_dir / rel(path, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return target


def write_bytes(path: Path, content: bytes) -> None:
    mode = None
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(content)
    if path.suffix in {".py", ".sh"}:
        os.chmod(path, (mode or 0o755) | 0o111)
    elif mode is not None:
        os.chmod(path, mode)


def copy_overlay(source: Path, target: Path, root: Path, backup_dir: Path, dry_run: bool) -> str:
    if not source.is_file():
        return f"! 缺少 overlay 文件：{source}"
    content = source.read_bytes()
    if target.exists() and target.read_bytes() == content:
        return f"= 已是最新：{rel(target, root)}"
    if dry_run:
        return f"[dry-run] 写入 {rel(target, root)}"
    saved = backup(target, root, backup_dir)
    write_bytes(target, content)
    suffix = f"（备份：{rel(saved, root)}）" if saved else ""
    return f"+ 写入 {rel(target, root)}{suffix}"


def install_agents_section(source: Path, root: Path, backup_dir: Path, dry_run: bool) -> str:
    target = root / "AGENTS.md"
    section = source.read_text(encoding="utf-8").rstrip() + "\n"
    marker = "## Native AI Mode"
    if target.exists():
        current = target.read_text(encoding="utf-8")
        if marker in current:
            return "= AGENTS.md 已包含 Native AI Mode"
        new_content = current.rstrip() + "\n\n" + section
        if dry_run:
            return "[dry-run] 追加 AGENTS.md Native AI Mode"
        saved = backup(target, root, backup_dir)
        target.write_text(new_content, encoding="utf-8")
        suffix = f"（备份：{rel(saved, root)}）" if saved else ""
        return f"+ 追加 AGENTS.md Native AI Mode{suffix}"
    if dry_run:
        return "[dry-run] 创建 AGENTS.md"
    write_bytes(target, section.encode("utf-8"))
    return "+ 创建 AGENTS.md"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 Native AI workflow 的配套文件写入已初始化的 Trellis 项目。",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help="显示帮助并退出")
    parser.add_argument("--root", default=None, help="目标项目根目录（默认当前目录）")
    parser.add_argument("--dry-run", action="store_true", help="只显示将要写入的文件")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve() if args.root else Path.cwd().resolve()
    if not (root / ".trellis").is_dir():
        print(f"错误：{root} 下没有 .trellis/，请先运行 trellis init。")
        return 1

    backup_dir = root / ".trellis" / f".backup-native-ai-{time.strftime('%Y%m%d-%H%M%S')}"
    results: list[str] = []
    seen: set[Path] = set()

    def add(source: Path, target: Path) -> None:
        if target.exists():
            real = target.resolve()
            if real in seen:
                return
            seen.add(real)
        results.append(copy_overlay(source, target, root, backup_dir, args.dry_run))

    # 脚本
    add(
        REPO_ROOT / "scripts" / "trellis-merge-new.py",
        root / "scripts" / "trellis-merge-new.py",
    )

    brainstorm = OVERLAY / "skills" / "trellis-brainstorm" / "SKILL.md"
    for target in BRAINSTORM_TARGETS:
        path = root / target
        if path.exists():
            add(brainstorm, path)

    start = OVERLAY / "skills" / "trellis-start" / "SKILL.md"
    for target in START_TARGETS:
        path = root / target
        if path.exists():
            add(start, path)

    continue_skill = OVERLAY / "skills" / "trellis-continue" / "SKILL.md"
    for target in CONTINUE_TARGETS:
        path = root / target
        if path.exists():
            add(continue_skill, path)

    for source_rel, target_rel in COMMAND_TARGETS:
        path = root / target_rel
        if path.exists():
            add(REPO_ROOT / source_rel, path)

    results.append(
        install_agents_section(
            OVERLAY / "AGENTS.native-ai.md", root, backup_dir, args.dry_run
        )
    )

    print(f"目标项目：{root}")
    if args.dry_run:
        print("模式：dry-run（不写入）")
    for line in results:
        print(f"  {line}")

    changed = sum(1 for line in results if line.startswith("+") or line.startswith("[dry-run] 写入"))
    if changed == 0:
        print("没有需要写入的文件。")
    elif args.dry_run:
        print("dry-run 结束。去掉 --dry-run 后执行写入。")
    else:
        print(f"完成。备份目录：{rel(backup_dir, root)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
