# -*- coding: utf-8 -*-
"""预置示例研判（跑好的数据）——让评委一打开就能看到完整结果，无需等待。"""
import os
import json
import glob

DEMO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "demos")
_ORDER = ["scam", "jobdd", "house", "gold", "btc", "ai"]


def list_demos():
    items = {}
    for p in glob.glob(os.path.join(DEMO_DIR, "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            items[d["id"]] = {"id": d["id"], "question": d["question"],
                              "headline": d.get("headline", ""), "tag": d.get("tag", ""),
                              "experts": d.get("experts", ""), "evidence": d.get("evidence", "")}
        except Exception:
            continue
    ordered = [items[k] for k in _ORDER if k in items]
    ordered += [v for k, v in items.items() if k not in _ORDER]
    return ordered


def get_demo(did):
    p = os.path.join(DEMO_DIR, f"{did}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)
