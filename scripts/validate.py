#!/usr/bin/env python3
"""校验 Trellis workflow marketplace 的 index.json 和 workflow.md 契约。

用法：
    python3 scripts/validate.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "index.json"
OPEN_RE = re.compile(r"^\[workflow-state:([A-Za-z0-9_-]+)\]\s*$", re.M)
CLOSE_RE = re.compile(r"^\[/workflow-state:([A-Za-z0-9_-]+)\]\s*$", re.M)
CORE_STATUSES = {"no_task", "planning", "in_progress"}


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def validate_index(errors: list[str]) -> list[dict]:
    if not INDEX_PATH.is_file():
        fail(errors, "缺少 index.json")
        return []
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(errors, f"index.json 不是合法 JSON：{exc}")
        return []
    if not isinstance(data, dict):
        fail(errors, "index.json 顶层必须是对象")
        return []
    if not isinstance(data.get("version"), int):
        fail(errors, "index.json 缺少整数 version")
    templates = data.get("templates")
    if not isinstance(templates, list) or not templates:
        fail(errors, "index.json 缺少非空 templates 数组")
        return []
    return [entry for entry in templates if isinstance(entry, dict)]


def validate_entry(entry: dict, errors: list[str], seen_ids: set[str]) -> Path | None:
    entry_id = entry.get("id")
    if not isinstance(entry_id, str) or not entry_id:
        fail(errors, f"模板条目缺少 id：{entry}")
        return None
    if entry_id in seen_ids:
        fail(errors, f"重复的 id：{entry_id}")
    seen_ids.add(entry_id)

    if entry.get("type") != "workflow":
        fail(errors, f"{entry_id}: type 必须是 workflow")
    if not isinstance(entry.get("name"), str) or not entry["name"]:
        fail(errors, f"{entry_id}: 缺少 name")
    description = entry.get("description")
    if description is not None and not isinstance(description, str):
        fail(errors, f"{entry_id}: description 必须是字符串")

    relative = entry.get("path")
    if not isinstance(relative, str) or not relative.endswith(".md"):
        fail(errors, f"{entry_id}: path 必须是 .md 文件")
        return None
    if ".." in Path(relative).parts:
        fail(errors, f"{entry_id}: path 不能包含 ..")
        return None
    target = (REPO_ROOT / relative).resolve()
    if REPO_ROOT not in target.parents:
        fail(errors, f"{entry_id}: path 必须位于仓库内")
        return None
    if not target.is_file():
        fail(errors, f"{entry_id}: 文件不存在：{relative}")
        return None
    return target


def validate_workflow(path: Path, errors: list[str]) -> set[str]:
    text = path.read_text(encoding="utf-8")
    stack: list[tuple[str, int]] = []
    statuses: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        opened = OPEN_RE.match(line)
        if opened:
            status = opened.group(1)
            stack.append((status, number))
            statuses.add(status)
            continue
        closed = CLOSE_RE.match(line)
        if closed:
            status = closed.group(1)
            if not stack:
                fail(errors, f"{path}: 第 {number} 行有闭合标签但没有对应开标签")
                continue
            opened_status, opened_line = stack.pop()
            if opened_status != status:
                fail(errors, f"{path}: 第 {opened_line} 行开标签 {opened_status} 与第 {number} 行闭标签 {status} 不匹配")
    if stack:
        status, number = stack[-1]
        fail(errors, f"{path}: 第 {number} 行的 [workflow-state:{status}] 没有闭合")
    for status in sorted(CORE_STATUSES - statuses):
        fail(errors, f"{path}: 缺少核心 breadcrumb 状态 {status}")
    return statuses


def main() -> int:
    errors: list[str] = []
    entries = validate_index(errors)
    seen_ids: set[str] = set()
    statuses_by_file: dict[Path, set[str]] = {}
    for entry in entries:
        workflow = validate_entry(entry, errors, seen_ids)
        if workflow is not None:
            statuses_by_file[workflow] = validate_workflow(workflow, errors)

    if errors:
        print("校验失败：")
        for message in errors:
            print(f"  - {message}")
        return 1

    print(f"OK：{len(statuses_by_file)} 个 workflow 模板")
    for workflow, statuses in sorted(statuses_by_file.items()):
        print(f"  - {workflow.relative_to(REPO_ROOT)}  ({len(statuses)} 个 breadcrumb 状态)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
