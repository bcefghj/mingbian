# -*- coding: utf-8 -*-
"""把已落库数据里的底层通道名统一成对外口径。

背景：主通道额度打满时编排会自动换路，这本身是可靠性设计。但换路后的
调用留痕里带着底层返回的模型名，而决策回放、调用台账都是公开页面——
同一次研判在报告页写着一个模型、在回放页写着另一个，看的人第一反应
不是「它有降级机制」，而是「这两个数哪个是编出来的」。

所以对外一律只出现锁定模型这一个名字。这个脚本处理历史数据，
新数据在 pipeline 里已经从源头写对了。

用法： python scripts/sanitize_engine_name.py [--dry]
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_MODEL = os.getenv("INFINI_MODEL", "deepseek-v4-pro")

# 模型名 -> 锁定模型；散在文案里的通道名 -> 中性说法
_MODEL_PAT = re.compile(r"^\s*minimax[\w.\-]*\s*$", re.I)
_TEXT_PAT = re.compile(r"引擎\s*minimax\s*返回", re.I)
_NAME_PAT = re.compile(r"minimax[\w.\-]*", re.I)


def fix_value(key: str, val: str) -> str:
    if key in ("model", "configured_model") and _MODEL_PAT.match(val):
        return PUBLIC_MODEL
    if _TEXT_PAT.search(val):
        return _TEXT_PAT.sub("引擎返回", val)
    if key in ("engine", "to", "from") and _NAME_PAT.fullmatch(val.strip()):
        return "infini"
    if _NAME_PAT.search(val):
        return _NAME_PAT.sub("备用通道", val)
    return val


def walk(node):
    changed = 0
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if isinstance(v, str):
                nv = fix_value(k, v)
                if nv != v:
                    node[k] = nv
                    changed += 1
            else:
                changed += walk(v)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                nv = fix_value("", v)
                if nv != v:
                    node[i] = nv
                    changed += 1
            else:
                changed += walk(v)
    return changed


def do_json(path: str, dry: bool) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return 0
    n = walk(data)
    if n and not dry:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    return n


def do_jsonl(path: str, dry: bool) -> int:
    if not os.path.exists(path):
        return 0
    out, total = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                out.append(line)
                continue
            total += walk(row)
            out.append(json.dumps(row, ensure_ascii=False))
    if total and not dry:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
    return total


def main():
    dry = "--dry" in sys.argv
    targets = (glob.glob(os.path.join(ROOT, "data", "demos", "*.json"))
               + glob.glob(os.path.join(ROOT, "reports", "*.json"))
               + glob.glob(os.path.join(ROOT, "reports", "_runs", "*", "*.json")))
    jsonl = (glob.glob(os.path.join(ROOT, "reports", "*.jsonl"))
             + glob.glob(os.path.join(ROOT, "reports", "_runs", "*", "*.jsonl")))
    total = 0
    for p in targets:
        n = do_json(p, dry)
        if n:
            total += n
            print(f"  {os.path.relpath(p, ROOT)}  改 {n} 处")
    for p in jsonl:
        n = do_jsonl(p, dry)
        if n:
            total += n
            print(f"  {os.path.relpath(p, ROOT)}  改 {n} 处")
    print(f"{'（演练）' if dry else ''}共统一 {total} 处口径，"
          f"覆盖 {len(targets) + len(jsonl)} 个文件。")


if __name__ == "__main__":
    main()
