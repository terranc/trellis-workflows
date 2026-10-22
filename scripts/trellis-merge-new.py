#!/usr/bin/env python3
"""逐个文件组审查并合并 Trellis 的 `.new` 文件。

使用流程：

    trellis upgrade
    trellis update -n          # 将上游版本写成 *.new，不动原文件
    python3 scripts/trellis-merge-new.py --llm

脚本不会自动合并。对每一组内容相同的 `.new` 文件，它会显示差异，
然后询问你要怎么处理：

    a  应用上游版本：先备份本地文件，再用 `.new` 覆盖，保存 baseline，
       删除 `.new`（会丢弃本地修改）
    m  标记已审查 / 保留本地：本地文件完全不动，把 `.new` 保存为
       新 baseline，删除 `.new`
    w  写入 AI 合并结果：把 LLM 生成的合并版本写回本地（会先备份）
    s  暂时跳过（保留 `.new`）
    d  查看完整 diff
    v  查看上游文件内容
    e  用 $EDITOR 打开本地文件（仅限单文件组）
    l  重新运行 LLM 分析
    q  退出

LLM 分析（可选，--llm）：加 --llm 后，每个分组会先自动调用本地
`dsh headless`，把本地文件、上游 `.new` 和 baseline 发给模型，让它判断
哪些上游改动是纯补充、哪些与本地定制冲突，并生成一份合并建议。建议会
显示在决策提示之前；只有你按 w 才会写入。加 --llm-manual 则改为按 l
手动触发。不传 --llm 时不会调用任何模型。

批量分析（--llm-batch，隐含 --llm）：先一次性分析所有分组，列出每个
文件属于“保留本地 / 可吸收 / 需人工”，然后选择：
    b  全部保留本地（等价于逐组按 m）
    w  对所有“可吸收”的文件生成并写入 AI 合并（逐文件确认）
    r  逐个文件处理
    s  全部跳过
    q  退出

Baseline：当文件被应用、标记为已审查，或写入 LLM 合并结果时，脚本会把
上游内容保存到 `.trellis/.upstream-baseline/`。下次运行时还会显示
`baseline -> .new`，也就是“自上次审查以来上游改了什么”。

成功时退出码为 0。跳过的 `.new` 文件会保留，下次运行继续提示。
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_ROOTS = (
    ".trellis",
    ".claude",
    ".codex",
    ".cursor",
    ".grok",
    ".opencode",
    ".pi",
    ".agents",
    ".skillshare",
    ".kimi-code",
    ".github",
)

EXCLUDE_PREFIXES = (
    ".git/",
    "node_modules/",
    ".trellis/.runtime/",
    ".trellis/.backup-",
    ".trellis/.upstream-baseline/",
    ".trellis/tasks/",
    ".trellis/workspace/",
    ".trellis/spec/",
)

MIXED_OWNERSHIP = {
    ".trellis/config.yaml",
    "AGENTS.md",
    ".github/copilot-instructions.md",
}

MAX_DIFF_LINES = 160
MAX_VIEW_LINES = 400


class ChineseArgumentParser(argparse.ArgumentParser):
    """把 argparse 自带的英文标题替换成中文。"""

    def format_help(self) -> str:
        return super().format_help().replace("usage:", "用法：").replace("options:", "选项：")



@dataclass
class Member:
    new_path: Path
    rel: str          # relative path of the original (without .new)
    original: Path
    new_bytes: bytes
    local_bytes: bytes | None


@dataclass
class Group:
    label: str
    digest: str
    members: list[Member] = field(default_factory=list)

    @property
    def new_bytes(self) -> bytes:
        return self.members[0].new_bytes


@dataclass
class MergeProposal:
    safe_to_merge: bool
    summary: str
    conflicts: list[str]
    merged_bytes: bytes


def find_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".trellis").is_dir():
            return candidate
    return current


def iter_new_files(root: Path) -> list[Path]:
    seen_dirs: set[Path] = set()
    seen_files: set[Path] = set()
    files: list[Path] = []

    def walk(directory: Path) -> None:
        real = directory.resolve()
        if real in seen_dirs:
            return
        seen_dirs.add(real)
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=True):
                    walk(path)
                elif entry.is_file(follow_symlinks=True) and entry.name.endswith(".new"):
                    real_file = path.resolve()
                    if real_file in seen_files:
                        continue
                    seen_files.add(real_file)
                    files.append(path)
            except OSError:
                continue

    for root_name in ALLOWED_ROOTS:
        base = root / root_name
        if base.exists():
            walk(base)

    # Root-level managed files live outside the allowed directories.
    for name in ("AGENTS.md", "CLAUDE.md"):
        candidate = root / (name + ".new")
        if candidate.is_file():
            real_file = candidate.resolve()
            if real_file not in seen_files:
                seen_files.add(real_file)
                files.append(candidate)
    return files


def relpath(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def is_excluded(rel: str, include_config: bool) -> bool:
    if any(rel.startswith(prefix) for prefix in EXCLUDE_PREFIXES):
        return True
    if rel == ".trellis/config.yaml.new" and not include_config:
        return True
    return False


def decode(data: bytes) -> str | None:
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def diff_lines(old: bytes, new: bytes, fromfile: str, tofile: str) -> list[str] | None:
    old_text = decode(old)
    new_text = decode(new)
    if old_text is None or new_text is None:
        return None
    return list(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
            n=3,
        )
    )


def diff_stats(lines: list[str]) -> tuple[int, int]:
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return added, removed


def print_diff(lines: list[str], limit: int = MAX_DIFF_LINES) -> None:
    for line in lines[:limit]:
        sys.stdout.write(line if line.endswith("\n") else line + "\n")
    if len(lines) > limit:
        print(f"... （还有 {len(lines) - limit} 行差异，按 d 查看全部）")


def backup(original: Path, root: Path, backup_dir: Path) -> Path | None:
    if not original.exists():
        return None
    target = backup_dir / relpath(original, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original, target)
    return target


def write_original(member: Member) -> None:
    target = member.original
    mode = None
    if target.exists():
        mode = stat.S_IMODE(target.stat().st_mode)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Opening the path follows a symlink, so shared skill files keep their
    # symlink and the real target is updated.
    with target.open("wb") as handle:
        handle.write(member.new_bytes)
    if mode is not None:
        os.chmod(target, mode)


def baseline_path(baseline_dir: Path, member: Member) -> Path:
    return baseline_dir / member.rel


def save_baseline(baseline_dir: Path | None, member: Member) -> None:
    if baseline_dir is None:
        return
    target = baseline_path(baseline_dir, member)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(member.new_bytes)


def group_new_files(root: Path, include_config: bool, only: str | None) -> list[Group]:
    groups: dict[str, Group] = {}
    for new_path in iter_new_files(root):
        rel_new = relpath(new_path, root)
        if is_excluded(rel_new, include_config):
            continue
        if only and only not in rel_new:
            continue
        rel = rel_new[: -len(".new")]
        original = root / rel
        try:
            new_bytes = new_path.read_bytes()
        except OSError as exc:
            print(f"! 无法读取 {rel_new}：{exc}")
            continue
        try:
            local_bytes = original.read_bytes() if original.exists() else None
        except OSError as exc:
            print(f"! 无法读取 {rel}：{exc}")
            continue
        digest = hashlib.sha256(new_bytes).hexdigest()
        group = groups.get(digest)
        if group is None:
            group = Group(label=rel, digest=digest)
            groups[digest] = group
        group.members.append(Member(new_path, rel, original, new_bytes, local_bytes))
    for group in groups.values():
        group.members.sort(key=lambda member: member.rel)
    return sorted(groups.values(), key=lambda group: group.members[0].rel)


def describe_local(member: Member) -> str:
    if member.local_bytes is None:
        return "本地缺失（将新建）"
    if member.local_bytes == member.new_bytes:
        return "已与上游一致"
    return "有差异"


def show_group(
    group: Group, index: int, total: int, baseline_dir: Path | None
) -> None:
    print()
    print("=" * 78)
    print(f"[{index}/{total}] {group.label}  （{len(group.members)} 个副本）")
    print("=" * 78)
    for member in group.members:
        suffix = ""
        if member.original.is_symlink():
            suffix = f"  （符号链接 -> {member.original.resolve()}）"
        print(f"  - {member.rel}{suffix}")
        print(f"    {describe_local(member)}")

    baseline_source = None
    if baseline_dir is not None:
        for member in group.members:
            candidate = baseline_path(baseline_dir, member)
            if candidate.is_file():
                baseline_source = (member, candidate)
                break

    if baseline_dir is None:
        print("\n相对 baseline 的上游变化：未启用 baseline。")
    elif baseline_source is not None:
        member, candidate = baseline_source
        baseline_bytes = candidate.read_bytes()
        upstream = diff_lines(
            baseline_bytes,
            group.new_bytes,
            f"{relpath(candidate, baseline_dir)}（上次审查）",
            f"{member.rel}.new（上游）",
        )
        if upstream is None:
            print("\n相对 baseline 的上游变化：二进制文件，无法显示 diff。")
        elif not upstream:
            print("\n相对 baseline 的上游变化：无变化。")
        else:
            added, removed = diff_stats(upstream)
            print(f"\n相对 baseline 的上游变化：+{added} / -{removed}")
    else:
        print("\n相对 baseline 的上游变化：尚无 baseline（首次审查）。")

    for member in group.members:
        lines = diff_lines(
            member.local_bytes or b"",
            member.new_bytes,
            member.rel,
            member.rel + ".new",
        )
        if lines is None:
            print(f"\n{member.rel}：二进制文件，无法显示 diff。")
        elif not lines:
            print(f"\n{member.rel}：本地无改动。")
        else:
            added, removed = diff_stats(lines)
            print(f"\n本地与上游差异：+{added} / -{removed}")
            print_diff(lines)


def prompt_action(llm_enabled: bool = False, has_proposal: bool = False) -> str:
    while True:
        write = " / [w] 写入 AI 合并结果" if has_proposal else ""
        analyze = " / [l] 重新分析" if llm_enabled else ""
        try:
            answer = input(
                "\n操作：[a] 应用上游（覆盖本地）/ [m] 标记已审查（保留本地）"
                f"{write} / [s] 跳过 / [d] 完整 diff / [v] 查看上游"
                f" / [e] 编辑本地{analyze} / [q] 退出（默认 s）："
            ).strip().lower()
        except EOFError:
            return "q"
        allowed = {"a", "m", "s", "d", "v", "e", "q"}
        if llm_enabled:
            allowed.add("l")
        if has_proposal:
            allowed.add("w")
        if answer in allowed:
            return answer
        if answer == "w" and not has_proposal:
            print("当前没有 AI 合并建议；先按 l 分析。")
            continue
        if answer == "l" and not llm_enabled:
            print("未启用 LLM 分析；请在启动时加 --llm。")
            continue
        if answer == "":
            return "s"
        choices = "a、m、s、d、v、e、q"
        if has_proposal:
            choices += "、w"
        if llm_enabled:
            choices += "、l"
        print(f"请输入 {choices}。")


def show_full_diff(group: Group) -> None:
    for member in group.members:
        lines = diff_lines(
            member.local_bytes or b"",
            member.new_bytes,
            member.rel,
            member.rel + ".new",
        )
        print()
        if lines is None:
            print(f"{member.rel}：二进制文件")
        elif not lines:
            print(f"{member.rel}：本地无改动")
        else:
            for line in lines:
                sys.stdout.write(line if line.endswith("\n") else line + "\n")


def show_new(group: Group) -> None:
    text = decode(group.new_bytes)
    print()
    if text is None:
        print("（二进制文件）")
        return
    lines = text.splitlines()
    for line in lines[:MAX_VIEW_LINES]:
        print(line)
    if len(lines) > MAX_VIEW_LINES:
        print(f"... （还有 {len(lines) - MAX_VIEW_LINES} 行）")


def edit_original(group: Group) -> None:
    if len(group.members) != 1:
        print("\n这一组有多个副本。请手动编辑，然后按 m。")
        for member in group.members:
            print(f"  {member.rel}")
        return
    member = group.members[0]
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    if not member.original.exists():
        print(f"\n{member.rel} 尚不存在；请先应用上游版本，再编辑。")
        return
    try:
        subprocess_editor = shutil.which(editor.split()[0])
        if subprocess_editor is None:
            print(f"\n找不到编辑器：{editor}")
            return
        subprocess.run([subprocess_editor, *editor.split()[1:], str(member.original)], check=False)
    except OSError as exc:
        print(f"\n无法打开编辑器：{exc}")
        return
    member.local_bytes = member.original.read_bytes()
    print(f"\n已编辑 {member.rel}。请查看下方的 diff。")


def apply_group(
    group: Group, root: Path, backup_dir: Path, baseline_dir: Path | None
) -> None:
    for member in group.members:
        saved = backup(member.original, root, backup_dir)
        write_original(member)
        save_baseline(baseline_dir, member)
        try:
            member.new_path.unlink()
        except OSError as exc:
            print(f"! 已应用 {member.rel}，但无法删除 {member.new_path}：{exc}")
        suffix = f"（备份：{relpath(saved, root)}）" if saved else ""
        print(f"  已应用 {member.rel}{suffix}")


def mark_group(group: Group, baseline_dir: Path | None) -> None:
    for member in group.members:
        save_baseline(baseline_dir, member)
        try:
            member.new_path.unlink()
        except OSError as exc:
            print(f"! 已标记 {member.rel}，但无法删除 {member.new_path}：{exc}")
        print(f"  已标记审查 {member.rel}（本地文件未改动）")


def call_dsh(prompt: str, timeout: int) -> str:
    try:
        proc = subprocess.run(
            ["dsh", "headless", "--json"],
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 dsh，请确认已安装并在 PATH 中") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"dsh 分析超时（{timeout}s）") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"dsh 退出码 {proc.returncode}：{detail[-500:]}")
    final = None
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "final" and isinstance(event.get("text"), str):
            final = event["text"]
    if final is None:
        raise RuntimeError("dsh 没有返回 final 事件")
    return final


def extract_json(text: str) -> dict | None:
    candidates = [text.strip()]
    for block in re.findall(r"```(?:json)?\s*(.*?)```", text, re.S):
        candidates.append(block.strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def build_llm_prompt(group: Group, member: Member, baseline_dir: Path | None) -> str:
    local = decode(member.local_bytes or b"") or ""
    upstream = decode(group.new_bytes) or ""
    baseline = "（无 baseline，首次审查）"
    if baseline_dir is not None:
        candidate = baseline_path(baseline_dir, member)
        if candidate.is_file():
            baseline = decode(candidate.read_bytes()) or ""
    return f"""你是 Trellis 工作流文件的合并助手。

文件路径：{member.rel}

这个仓库使用 Native AI 模式：Trellis 只负责对齐 PRD 和 design，代码由主会话直接实现；
不创建 implement.md，不派发 implement/check 子代理。本地文件中的这类定制是有意保留的。

请对比下面三份内容：

本地文件（包含用户有意保留的定制）：
<<<LOCAL
{local}
LOCAL>>>

上游新版本：
<<<UPSTREAM
{upstream}
UPSTREAM>>>

上次审查的上游基线：
<<<BASELINE
{baseline}
BASELINE>>>

任务：
1. 找出上游的纯补充内容（新增章节、说明、示例、命令等），它们不与本地定制冲突，应该吸收。
2. 找出与本地定制冲突的改动（修改了本地定制所在的段落），冲突时保留本地行为。
3. 生成合并后的完整文件：保留本地 Native AI 行为，同时吸收不冲突的上游补充。
4. 只输出严格 JSON，不要 markdown 代码块，不要额外解释。格式：
{{"safe_to_merge": true, "summary": "一句话说明", "conflicts": ["冲突点"], "merged_content": "完整文件内容"}}

merged_content 必须是完整文件内容，保留原有换行和格式。
"""


def llm_merge(group: Group, baseline_dir: Path | None, timeout: int) -> MergeProposal | None:
    local_variants = {member.local_bytes for member in group.members}
    if len(local_variants) > 1:
        print("\n这一组多个副本的本地内容不一致，无法统一分析。请用 --only 逐个处理。")
        return None
    member = group.members[0]
    print("\n正在调用本地 dsh 分析（可能需要几十秒）...")
    try:
        raw = call_dsh(build_llm_prompt(group, member, baseline_dir), timeout)
    except RuntimeError as exc:
        print(f"! LLM 分析失败：{exc}")
        return None
    data = extract_json(raw)
    if data is None:
        print("! 无法从 dsh 输出中解析 JSON。原始输出：")
        print(raw[:2000])
        return None
    merged = data.get("merged_content")
    if not isinstance(merged, str) or not merged.strip():
        print("! LLM 没有返回有效的 merged_content。")
        return None
    if not merged.endswith("\n"):
        merged += "\n"
    conflicts = data.get("conflicts") or []
    if not isinstance(conflicts, list):
        conflicts = [str(conflicts)]
    return MergeProposal(
        safe_to_merge=bool(data.get("safe_to_merge")),
        summary=str(data.get("summary") or ""),
        conflicts=[str(item) for item in conflicts],
        merged_bytes=merged.encode("utf-8"),
    )


def build_batch_prompt(groups: list[Group], baseline_dir: Path | None) -> str:
    sections = []
    for index, group in enumerate(groups, start=1):
        member = group.members[0]
        local = decode(member.local_bytes or b"") or ""
        upstream = decode(group.new_bytes) or ""
        baseline = "（无 baseline）"
        if baseline_dir is not None:
            candidate = baseline_path(baseline_dir, member)
            if candidate.is_file():
                baseline = decode(candidate.read_bytes()) or ""
        copies = f"（{len(group.members)} 个副本）" if len(group.members) > 1 else ""
        sections.append(
            f"### 文件 {index}: {member.rel} {copies}\n"
            f"本地文件：\n<<<LOCAL\n{local}\nLOCAL>>>\n"
            f"上游新版本：\n<<<UPSTREAM\n{upstream}\nUPSTREAM>>>\n"
            f"上次审查的 baseline：\n<<<BASELINE\n{baseline}\nBASELINE>>>\n"
        )
    return (
        "你是 Trellis 工作流文件的批量合并分析助手。\n\n"
        "这个仓库使用 Native AI 模式：Trellis 只负责对齐 PRD 和 design，代码由主会话直接实现；\n"
        "不创建 implement.md，不派发 implement/check 子代理。本地文件中的这类定制是有意保留的。\n\n"
        "请逐个分析下面的文件，判断上游改动与本地定制的关系：\n"
        "- keep_local：上游改动全部落在本地定制上，或没有值得吸收的新增内容 → 保留本地。\n"
        "- merge：上游包含不冲突的纯补充（新增章节、说明、示例、命令）→ 可以吸收。\n"
        "- review：无法确定，需要人工判断。\n\n"
        "只输出严格 JSON，不要 markdown 代码块，不要额外解释。格式：\n"
        '{"files":[{"path":"文件路径","recommendation":"keep_local|merge|review",'
        '"safe_to_merge":true,"summary":"一句话说明","conflicts":["冲突点"]}]}\n\n'
        + "\n".join(sections)
    )


def lookup_batch_rec(recs: dict[str, dict], member: Member) -> dict:
    rec = recs.get(member.rel)
    if rec is not None:
        return rec
    basename = Path(member.rel).name
    for path, value in recs.items():
        if Path(path).name == basename:
            return value
    return {
        "recommendation": "review",
        "safe_to_merge": False,
        "summary": "模型未返回该文件的判断",
        "conflicts": [],
    }


def llm_batch_analyze(
    groups: list[Group], baseline_dir: Path | None, timeout: int
) -> dict[str, dict] | None:
    prompt = build_batch_prompt(groups, baseline_dir)
    if len(prompt) > 220_000:
        print(
            f"批量分析内容过大（{len(prompt)} 字符），"
            "请改用 --llm 逐组分析，或用 --only 分批。"
        )
        return None
    print("\n正在调用本地 dsh 批量分析（文件较多时可能需要一两分钟）...")
    try:
        raw = call_dsh(prompt, timeout)
    except RuntimeError as exc:
        print(f"! 批量分析失败：{exc}")
        return None
    data = extract_json(raw)
    if data is None:
        print("! 无法从 dsh 输出中解析 JSON。原始输出：")
        print(raw[:2000])
        return None
    files = data.get("files")
    items: list = []
    if isinstance(files, list):
        items = files
    elif isinstance(files, dict):
        for path, value in files.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("path", path)
                items.append(item)
    if not items:
        print("! 批量分析没有返回 files 列表。")
        return None

    result: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip().strip("`")
        if not path:
            continue
        recommendation = str(item.get("recommendation") or "review").strip().lower()
        if recommendation in {"keep", "keep-local", "keep_local", "local"}:
            recommendation = "keep_local"
        elif recommendation in {"merge", "safe", "additive", "apply"}:
            recommendation = "merge"
        else:
            recommendation = "review"
        conflicts = item.get("conflicts") or []
        if not isinstance(conflicts, list):
            conflicts = [str(conflicts)]
        result[path] = {
            "recommendation": recommendation,
            "safe_to_merge": bool(item.get("safe_to_merge")),
            "summary": str(item.get("summary") or ""),
            "conflicts": [str(value) for value in conflicts],
        }
    return result or None


def show_batch_summary(groups: list[Group], recs: dict[str, dict]) -> None:
    labels = {"keep_local": "保留本地", "merge": "可吸收", "review": "需人工"}
    print("\n批量分析结果：")
    for index, group in enumerate(groups, start=1):
        member = group.members[0]
        rec = lookup_batch_rec(recs, member)
        copies = f"（{len(group.members)} 个副本）" if len(group.members) > 1 else ""
        print(f"  {index}. [{labels[rec['recommendation']]}] {member.rel}{copies}")
        if rec["summary"]:
            print(f"     {rec['summary']}")
        for conflict in rec["conflicts"][:3]:
            print(f"     冲突：{conflict}")
    merge_count = sum(
        1
        for group in groups
        if lookup_batch_rec(recs, group.members[0])["recommendation"] == "merge"
    )
    print(f"\n可吸收的文件：{merge_count} 个；其余建议保留本地或人工判断。")


def prompt_batch_action() -> str:
    print("\n批量操作：")
    print("  [b] 全部保留本地（逐组标记已审查）")
    print("  [w] 写入所有“可吸收”的 AI 合并（逐文件确认）")
    print("  [r] 逐个文件处理")
    print("  [s] 全部跳过")
    print("  [q] 退出")
    while True:
        try:
            answer = input("选择（默认 r）：").strip().lower()
        except EOFError:
            return "q"
        if answer in {"b", "w", "r", "s", "q"}:
            return answer
        if answer == "":
            return "r"
        print("请输入 b、w、r、s 或 q。")


def show_proposal(group: Group, proposal: MergeProposal) -> None:
    print("\nLLM 分析结果：")
    print(f"  safe_to_merge: {proposal.safe_to_merge}")
    if proposal.summary:
        print(f"  摘要：{proposal.summary}")
    if proposal.conflicts:
        print("  冲突：")
        for item in proposal.conflicts:
            print(f"    - {item}")
    else:
        print("  冲突：无")
    member = group.members[0]
    lines = diff_lines(
        member.local_bytes or b"",
        proposal.merged_bytes,
        member.rel,
        member.rel + "（LLM 合并）",
    )
    if lines is None:
        print("  （二进制文件，无法显示 diff）")
    elif not lines:
        print("  合并结果与本地一致。")
    else:
        added, removed = diff_stats(lines)
        print(f"\n本地 → LLM 合并：+{added} / -{removed}")
        print_diff(lines)


def apply_merged_group(
    group: Group,
    root: Path,
    backup_dir: Path,
    baseline_dir: Path | None,
    merged_bytes: bytes,
) -> None:
    for member in group.members:
        saved = backup(member.original, root, backup_dir)
        member.local_bytes = merged_bytes
        write_original(member)
        # baseline 记录上游版本，而不是合并后的本地版本。
        save_baseline(baseline_dir, member)
        try:
            member.new_path.unlink()
        except OSError as exc:
            print(f"! 已写入 {member.rel}，但无法删除 {member.new_path}：{exc}")
        suffix = f"（备份：{relpath(saved, root)}）" if saved else ""
        print(f"  已写入 LLM 合并结果 {member.rel}{suffix}")


def main() -> int:
    parser = ChineseArgumentParser(
        description="逐个文件组审查 Trellis 的 *.new 文件，应用或标记为已审查。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help="显示帮助并退出")
    parser.add_argument("--root", default=None, help="仓库根目录（默认向上查找 .trellis）")
    parser.add_argument("--dry-run", action="store_true", help="只显示分组和 diff，不写入文件")
    parser.add_argument("--yes", action="store_true", help="不做询问，应用所有分组")
    parser.add_argument("--only", default=None, help="只处理路径中包含该子串的文件")
    parser.add_argument(
        "--include-config",
        action="store_true",
        help="包含 .trellis/config.yaml.new（默认排除；混合所有权文件）",
    )
    parser.add_argument(
        "--baseline-dir",
        default=".trellis/.upstream-baseline",
        help="baseline 目录（默认 .trellis/.upstream-baseline）",
    )
    parser.add_argument("--no-baseline", action="store_true", help="不读取也不写入 baseline")
    parser.add_argument("--list", action="store_true", help="列出 .new 文件后退出")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="启用本地 dsh 分析：每个分组先自动分析，再让你决策",
    )
    parser.add_argument(
        "--llm-manual",
        action="store_true",
        help="配合 --llm：只按 l 手动分析，不自动分析",
    )
    parser.add_argument(
        "--llm-batch",
        action="store_true",
        help="先一次性分析所有分组，再选择批量或逐个处理（隐含 --llm）",
    )
    parser.add_argument(
        "--llm-timeout",
        type=int,
        default=180,
        help="dsh 分析超时秒数（默认 180）",
    )
    args = parser.parse_args()

    if args.llm_batch:
        args.llm = True

    root = find_root(Path(args.root)) if args.root else find_root(Path.cwd())
    baseline_dir = None if args.no_baseline else root / args.baseline_dir
    llm_auto = args.llm and not args.llm_manual and not args.llm_batch

    if args.llm:
        if args.llm_batch:
            mode = "批量（先一次性分析所有分组）"
        elif args.llm_manual:
            mode = "按需（按 l 触发）"
        else:
            mode = "自动（每个分组先分析）"
        print(f"已启用本地 dsh {mode}分析：会把文件内容发送给 dsh 配置的模型；")
        print("LLM 只生成合并建议，写入前仍需你确认。")
        if args.yes:
            print("注意：--yes 不做询问，将直接应用上游，不会运行 LLM 分析。")

    groups = group_new_files(root, args.include_config, args.only)
    if not groups:
        print("没有找到 Trellis 的 .new 文件。")
        print("请先运行 `trellis update -n`，再重新执行本脚本。")
        return 0

    if args.list:
        for group in groups:
            for member in group.members:
                print(member.rel)
        return 0

    if not args.include_config and (root / ".trellis/config.yaml.new").is_file():
        print("注意：已忽略 .trellis/config.yaml.new（混合所有权文件）。")
        print("      请手动审查，或加 --include-config 将其纳入。")

    backup_dir = root / ".trellis" / f".backup-merge-{time.strftime('%Y%m%d-%H%M%S')}"
    applied = marked = skipped = 0

    total = len(groups)
    skip_loop = False

    if args.llm_batch and not args.dry_run and not args.yes:
        recs = llm_batch_analyze(groups, baseline_dir, args.llm_timeout)
        if recs is None:
            print("批量分析失败，改为逐组自动分析。")
            llm_auto = True
        else:
            show_batch_summary(groups, recs)
            action = prompt_batch_action()
            if action == "q":
                print("\n已退出。剩余的 .new 文件未改动。")
                skip_loop = True
            elif action == "s":
                skipped += total
                print("已跳过所有分组，.new 文件保留。")
                skip_loop = True
            elif action == "b":
                for group in groups:
                    mark_group(group, baseline_dir)
                marked += total
                print(f"已标记审查 {total} 个分组（本地文件未改动）。")
                skip_loop = True
            elif action == "w":
                for group in groups:
                    member = group.members[0]
                    rec = lookup_batch_rec(recs, member)
                    if rec["recommendation"] != "merge":
                        skipped += 1
                        continue
                    proposal = llm_merge(group, baseline_dir, args.llm_timeout)
                    if proposal is None:
                        skipped += 1
                        continue
                    show_proposal(group, proposal)
                    try:
                        confirm = input("写入这个合并结果？[y/N]：").strip().lower()
                    except EOFError:
                        confirm = "n"
                    if confirm == "y":
                        apply_merged_group(
                            group, root, backup_dir, baseline_dir, proposal.merged_bytes
                        )
                        applied += 1
                    else:
                        skipped += 1
                print("未写入的分组保留 .new，可重新运行逐个处理。")
                skip_loop = True
            else:
                llm_auto = False
                print("进入逐个文件处理模式。")

    if skip_loop:
        groups = []

    for index, group in enumerate(groups, start=1):
        show_group(group, index, total, baseline_dir)
        if args.dry_run:
            continue

        if args.yes:
            if any(member.rel in MIXED_OWNERSHIP for member in group.members):
                print("混合所有权文件在 --yes 模式下已跳过。")
                skipped += 1
                continue
            apply_group(group, root, backup_dir, baseline_dir)
            applied += 1
            continue

        proposal = None
        if llm_auto:
            proposal = llm_merge(group, baseline_dir, args.llm_timeout)
            if proposal is not None:
                show_proposal(group, proposal)

        quit_requested = False
        while True:
            action = prompt_action(args.llm, proposal is not None)

            if action == "q":
                print("\n已退出。剩余的 .new 文件未改动。")
                quit_requested = True
                break
            if action == "a":
                if any(member.rel in MIXED_OWNERSHIP for member in group.members):
                    print("\n这是混合所有权文件；整文件应用会覆盖本地设置。")
                    print("建议手动合并，然后按 m。")
                    try:
                        confirm = input("仍要应用吗？[y/N]：").strip().lower()
                    except EOFError:
                        confirm = "n"
                    if confirm != "y":
                        continue
                apply_group(group, root, backup_dir, baseline_dir)
                applied += 1
                break
            if action == "m":
                mark_group(group, baseline_dir)
                marked += 1
                break
            if action == "w":
                if proposal is None:
                    print("当前没有 AI 合并建议；先按 l 分析。")
                    continue
                apply_merged_group(
                    group, root, backup_dir, baseline_dir, proposal.merged_bytes
                )
                applied += 1
                break
            if action == "l":
                proposal = llm_merge(group, baseline_dir, args.llm_timeout)
                if proposal is not None:
                    show_proposal(group, proposal)
                continue
            if action == "d":
                show_full_diff(group)
                continue
            if action == "v":
                show_new(group)
                continue
            if action == "e":
                edit_original(group)
                continue
            if action == "s":
                skipped += 1
                break

        if quit_requested:
            break

    print("\n" + "-" * 78)
    if args.dry_run:
        print(f"试运行：共 {total} 组，未写入任何文件。")
    else:
        print(f"已应用：{applied}  已标记审查：{marked}  已跳过：{skipped}")
        if applied:
            print(f"备份：{relpath(backup_dir, root)}/")
        if skipped:
            print("跳过的 .new 文件会保留，下次运行继续提示。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
