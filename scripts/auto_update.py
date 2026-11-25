#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""华尔街渣宝 · 自动版本检测与自动更新脚本（零第三方依赖）

用法：
  python3 scripts/auto_update.py                 # 默认 6 小时冷却内跳过联网；发现新版自动更新
  python3 scripts/auto_update.py --force         # 忽略冷却，立即联网检测并自动更新
  python3 scripts/auto_update.py --check-only    # 仅检测不写入
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_OWNER = "zhashushu"
REPO_NAME = "wallstreet-zhabao"
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}"
RAW_SKILL_URL = f"{RAW_BASE}/SKILL.md"

SKILL_DIR = Path(__file__).resolve().parent.parent
LOCAL_SKILL = SKILL_DIR / "SKILL.md"
STAMP_FILE = SKILL_DIR / ".last_update_check"
COOLDOWN_SECONDS = 6 * 3600  # 6 小时内不重复联网，避免每次分析都等待网络
TIMEOUT_SECONDS = 6

SYNC_FILES = [
    "SKILL.md",
    "README.md",
    ".gitignore",
    "references/anti_drift.md",
    "references/data_sources.md",
    "references/methodology.md",
    "references/output_template.md",
    "scripts/valuation.py",
    "scripts/auto_update.py",
]


def parse_version(text: str) -> str:
    """从 YAML frontmatter 提取 version 字段，缺省返回 0.0"""
    m = re.search(r'^version:\s*["\']?([0-9]+(?:\.[0-9]+)*)["\']?', text[:1500], re.M)
    return m.group(1) if m else "0.0"


def ver_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)) or (0,)


def fetch_text(url: str, timeout: int = TIMEOUT_SECONDS) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "wallstreet-zhabao-updater/1.0", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def write_stamp(status: str, local_ver: str, remote_ver: str = "") -> None:
    try:
        STAMP_FILE.write_text(
            json.dumps(
                {
                    "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "ts": int(time.time()),
                    "status": status,
                    "local_version": local_ver,
                    "remote_version": remote_ver,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def within_cooldown() -> bool:
    if not STAMP_FILE.exists():
        return False
    try:
        data = json.loads(STAMP_FILE.read_text(encoding="utf-8"))
        return (time.time() - int(data.get("ts", 0))) < COOLDOWN_SECONDS
    except Exception:
        return False


def is_git_repo() -> bool:
    return (SKILL_DIR / ".git").exists()


def git_is_dirty() -> bool:
    r = subprocess.run(
        ["git", "-C", str(SKILL_DIR), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    return bool(r.stdout.strip())


def backup_local_skill(old_ver: str) -> Path | None:
    if not LOCAL_SKILL.exists():
        return None
    bak = SKILL_DIR / f"SKILL.md.{old_ver}.bak"
    if not bak.exists():
        shutil.copy2(LOCAL_SKILL, bak)
    return bak


def update_via_git(old_ver: str) -> tuple[bool, str]:
    if git_is_dirty():
        return False, "本地存在未提交修改，已跳过自动覆盖以防丢失改动（请手动提交或合并）"
    backup_local_skill(old_ver)
    r = subprocess.run(
        ["git", "-C", str(SKILL_DIR), "pull", "--ff-only", "origin", BRANCH],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if r.returncode == 0:
        return True, "git pull --ff-only 成功"
    return False, f"git pull 失败：{r.stderr.strip() or r.stdout.strip()}"


def update_via_raw(old_ver: str, remote_skill_text: str) -> tuple[bool, str]:
    backup_local_skill(old_ver)
    updated = []
    for rel in SYNC_FILES:
        dst = SKILL_DIR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = remote_skill_text if rel == "SKILL.md" else fetch_text(f"{RAW_BASE}/{rel}")
            dst.write_text(content, encoding="utf-8")
            updated.append(rel)
        except Exception:
            if rel == "SKILL.md":
                return False, "写入 SKILL.md 失败"
    return True, f"已通过 GitHub Raw 同步 {len(updated)} 个文件"


def main() -> int:
    ap = argparse.ArgumentParser(description="华尔街渣宝自动版本检测与更新")
    ap.add_argument("--force", action="store_true", help="忽略 6 小时冷却，立即联网检测")
    ap.add_argument("--check-only", action="store_true", help="仅检测版本，不自动更新")
    args = ap.parse_args()

    local_text = LOCAL_SKILL.read_text(encoding="utf-8") if LOCAL_SKILL.exists() else ""
    local_ver = parse_version(local_text)

    if not args.force and within_cooldown():
        print(f"SKIP_COOLDOWN local=v{local_ver}")
        return 0

    try:
        remote_text = fetch_text(RAW_SKILL_URL)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"OFFLINE_SKIP local=v{local_ver} reason={type(e).__name__}")
        return 0

    remote_ver = parse_version(remote_text)
    lv, rv = ver_tuple(local_ver), ver_tuple(remote_ver)

    if rv < lv:
        write_stamp("LOCAL_AHEAD", local_ver, remote_ver)
        print(f"LOCAL_AHEAD local=v{local_ver} remote=v{remote_ver}")
        return 0

    if rv == lv:
        # 版本号一致时，检查非空白正文是否有补丁差异（仅在 git 干净且非 check-only 时 fast-forward）
        if remote_text.strip() == local_text.strip():
            write_stamp("UP_TO_DATE", local_ver, remote_ver)
            print(f"UP_TO_DATE version=v{local_ver}")
            return 0
        if args.check_only:
            write_stamp("PATCH_AVAILABLE", local_ver, remote_ver)
            print(f"PATCH_AVAILABLE local=v{local_ver} remote=v{remote_ver} (content differs)")
            return 0

    if args.check_only:
        write_stamp("UPDATE_AVAILABLE", local_ver, remote_ver)
        print(f"UPDATE_AVAILABLE local=v{local_ver} remote=v{remote_ver}")
        return 0

    # 执行自动更新
    if is_git_repo():
        ok, msg = update_via_git(local_ver)
    else:
        ok, msg = update_via_raw(local_ver, remote_text)

    if ok:
        new_ver = parse_version(LOCAL_SKILL.read_text(encoding="utf-8"))
        write_stamp("UPDATED", new_ver, remote_ver)
        print(f"UPDATED v{local_ver} -> v{new_ver} ({msg})")
        return 0

    write_stamp("UPDATE_SKIPPED", local_ver, remote_ver)
    print(f"UPDATE_SKIPPED local=v{local_ver} remote=v{remote_ver} ({msg})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
