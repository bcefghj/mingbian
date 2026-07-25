# -*- coding: utf-8 -*-
"""从已落库的示例 / 报告回填调用台账。

台账页要回答的是「编排真实发出了多少次推理」。示例与报告的 trace / calls
里已经记着这些动作，但部署时 reports/ 不进包，服务器台账就会是空的——
这一页也就看起来像没跑过。

本脚本只回填真实发生过的调用（有耗时、有产出字数的引擎 span，或 pipeline
记进 calls 的条目），不编造 taskId。平台回写了回执号的那些继续保留。

用法：
  ./.venv/bin/python scripts/backfill_ledger.py
  ./.venv/bin/python scripts/backfill_ledger.py --reset   # 先清空再回填
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import store  # noqa: E402

PURPOSE_CN = {
    "analyze": "慎思 · 成文",
    "rework": "返工 · 重推",
    "audit": "质检 · 五维评审",
    "debate_attack": "明辨 · 红队攻击",
    "debate_judge": "明辨 · 裁判裁定",
    "deepen": "深化追问",
}

# 只有这些 purpose 才算「打过引擎」，路由器 / 取证核验之类不算
ENGINE_PURPOSES = set(PURPOSE_CN)


def _load_docs() -> list[dict]:
    docs = []
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "demos", "*.json"))):
        try:
            docs.append(json.load(open(p, encoding="utf-8")))
        except Exception:
            continue
    for p in sorted(glob.glob(os.path.join(store.REPORT_DIR, "*.json"))):
        try:
            docs.append(json.load(open(p, encoding="utf-8")))
        except Exception:
            continue
    # 按 id 去重，报告优先（通常更新）
    by_id: dict[str, dict] = {}
    for d in docs:
        rid = d.get("id") or ""
        if not rid:
            continue
        by_id[rid] = d
    return list(by_id.values())


def _from_calls(d: dict) -> list[dict]:
    out = []
    q = (d.get("question") or "")[:120]
    rid = d.get("id") or ""
    mode = (d.get("mode_config") or {}).get("key") or d.get("mode") or ""
    model = d.get("model") or "deepseek-v4-pro"
    base_ts = int(d.get("created_at") or d.get("seeded_at") or time.time())
    for i, c in enumerate(d.get("calls") or []):
        purpose = c.get("purpose") or ""
        if purpose not in ENGINE_PURPOSES and purpose not in PURPOSE_CN.values():
            continue
        out.append({
            "ts": base_ts + i,  # 同一次研判内按顺序错开一秒，台账好看
            "taskId": c.get("taskId") or "",
            "model": model,
            "engine": "infini",
            "question": q,
            "report_id": rid,
            "share_url": c.get("share_url") or d.get("share_url") or "",
            "elapsed_ms": int(c.get("elapsed_ms") or 0),
            "mode": mode,
            "status": "ok",
            "purpose": PURPOSE_CN.get(purpose, purpose),
            "agent": c.get("agent") or "",
            "prompt_chars": int(c.get("prompt_chars") or 0),
            "output_chars": int(c.get("output_chars") or 0),
        })
    return out


def _from_spans(d: dict) -> list[dict]:
    """没有 calls 字段的旧示例，从 trace.spans 里捞真正打过引擎的那些。"""
    out = []
    q = (d.get("question") or "")[:120]
    rid = d.get("id") or ""
    mode = (d.get("mode_config") or {}).get("key") or d.get("mode") or ""
    model = d.get("model") or "deepseek-v4-pro"
    base_ts = int(d.get("created_at") or d.get("seeded_at") or time.time())
    spans = (d.get("trace") or {}).get("spans") or []
    i = 0
    for sp in spans:
        purpose = sp.get("purpose") or ""
        if purpose not in ENGINE_PURPOSES:
            continue
        latency = int(sp.get("latency_ms") or 0)
        out_chars = int(sp.get("output_chars") or 0)
        # 过滤掉失败 / 瞬间返回的空调用
        if latency < 800 or out_chars < 40:
            continue
        out.append({
            "ts": base_ts + i,
            "taskId": "",
            "model": model,
            "engine": "infini",
            "question": q,
            "report_id": rid,
            "share_url": d.get("share_url") or "",
            "elapsed_ms": latency,
            "mode": mode,
            "status": "ok",
            "purpose": PURPOSE_CN.get(purpose, purpose),
            "agent": sp.get("agent_id") or sp.get("agent") or "",
            "prompt_chars": int(sp.get("prompt_chars") or 0),
            "output_chars": out_chars,
        })
        i += 1
    return out


def _dedupe_key(r: dict) -> str:
    # 同一报告 + 环节 + 耗时 + 产出，视为同一条（避免重复回填）
    return "|".join([
        str(r.get("report_id") or ""),
        str(r.get("purpose") or ""),
        str(r.get("elapsed_ms") or 0),
        str(r.get("output_chars") or 0),
        str(r.get("taskId") or ""),
    ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="先清空现有台账再回填")
    args = ap.parse_args()

    existing = [] if args.reset else store.read_ledger(5000)
    # read_ledger 是新→旧，翻回来按时间排
    existing = list(reversed(existing))
    seen = {_dedupe_key(r) for r in existing}

    added = 0
    docs = _load_docs()
    for d in docs:
        rows = _from_calls(d) or _from_spans(d)
        # 顶层那一次成文的 taskId，补到第一条慎思成文上（如果还空着）
        top_tid = d.get("taskId") or ""
        if top_tid:
            for r in rows:
                if r["purpose"] == "慎思 · 成文" and not r["taskId"]:
                    r["taskId"] = top_tid
                    break
        for r in rows:
            k = _dedupe_key(r)
            if k in seen:
                continue
            seen.add(k)
            existing.append(r)
            added += 1

    existing.sort(key=lambda r: r.get("ts") or 0)
    os.makedirs(os.path.dirname(store.LEDGER_PATH), exist_ok=True)
    with open(store.LEDGER_PATH, "w", encoding="utf-8") as f:
        for r in existing:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    stats = store.ledger_stats()
    print(f"新增 {added} 条，台账合计 {stats['total_calls']} 条"
          f"（覆盖 {stats['runs']} 次研判 · 平均每次 {stats['calls_per_run']} 打 · "
          f"带回执号 {stats['with_task_id']}）")
    for p in stats.get("by_purpose") or []:
        print(f"  {p['purpose']:<16} {p['count']:>3} 次 · 均 {p['avg_ms']/1000:.1f}s")


if __name__ == "__main__":
    main()
