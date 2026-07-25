# -*- coding: utf-8 -*-
"""Case Benchmark：10 道固定研判题。

存在的理由很直接——单个 demo 跑得漂亮说明不了什么，可能只是碰巧。
固定题集 + 版本曲线，才能证明「这一版比上一版好」是工程结果而不是运气。
"""
from __future__ import annotations

import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(__file__))
BENCH_PATH = os.path.join(ROOT, "data", "bench.jsonl")
os.makedirs(os.path.dirname(BENCH_PATH), exist_ok=True)

CASES = [
    {"id": "house", "q": "现在这个时点，中国一线城市房价还会继续跌吗？",
     "domain": "楼市", "hard": "政策与数据都在变，容易写成正确的废话"},
    {"id": "gold", "q": "黄金现在还能追高吗？",
     "domain": "贵金属", "hard": "必须锚定实时价格，最容易暴露时间错乱"},
    {"id": "btc", "q": "比特币这轮周期见顶了吗？",
     "domain": "加密资产", "hard": "多空证据都很足，考验张力呈现"},
    {"id": "ai", "q": "AI 算力投资是不是已经进入泡沫阶段？",
     "domain": "科技产业", "hard": "考验基准率与历史对照"},
    {"id": "jobdd", "q": "一家 C 轮创业公司给了 offer，值不值得从大厂跳过去？",
     "domain": "公司尽调", "hard": "信息高度不对称，考验缺口诚实度"},
    {"id": "scam", "q": "有人推荐一个「日返 1.5%、稳赚不赔」的理财项目，是骗局吗？",
     "domain": "反诈", "hard": "结论明确但必须给出可核验依据而非常识断言"},
    {"id": "reno", "q": "全包装修报价每平米 2800 元，是不是被宰了？",
     "domain": "消费维权", "hard": "地域差异大，考验条件化表述"},
    {"id": "study", "q": "现在花 60 万去英国读一年硕士，性价比如何？",
     "domain": "教育决策", "hard": "回报难量化，容易滑向鸡汤"},
    {"id": "shop", "q": "在二线城市商圈开一家精品咖啡店，能回本吗？",
     "domain": "创业可行性", "hard": "考验成本结构拆解与失败率引用"},
    {"id": "health", "q": "网上说隔夜菜致癌，这个说法站得住吗？",
     "domain": "健康传闻", "hard": "考验剂量与条件，不能一刀切"},
]

_BY_ID = {c["id"]: c for c in CASES}


def get_case(cid: str) -> dict | None:
    return _BY_ID.get(cid)


def record(case_id: str, version: str, payload: dict):
    """跑完一道题后记一行。字段全部来自真实运行结果。"""
    q = payload.get("quality") or {}
    m = (payload.get("metrics") or {}).get("raw") or {}
    claims = payload.get("claims") or []
    row = {
        "ts": int(time.time()), "case_id": case_id, "version": version,
        "report_id": payload.get("id", ""),
        "evidence_count": q.get("evidence_count", 0),
        "independent_domains": q.get("independent_domains", 0),
        "claim_count": q.get("claim_count", 0),
        "unsupported_ratio": round((q.get("unsupported_claims", 0) /
                                    max(1, q.get("claim_count", 1))), 3),
        "bound_ratio": q.get("evidence_bound_ratio", 0),
        "avg_credibility": q.get("avg_credibility", 0),
        "rework_rounds": q.get("rounds", 0),
        "gate_pass_first_try": q.get("rounds", 0) == 0,
        "elapsed_ms": payload.get("elapsed_ms", 0),
        "tensions": len(payload.get("tensions") or []),
        "gaps": len(payload.get("gaps") or []),
        "strong_claims": sum(1 for c in claims if c.get("strength") == "strong"),
    }
    try:
        with open(BENCH_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return row


def read_rows() -> list[dict]:
    if not os.path.exists(BENCH_PATH):
        return []
    rows = []
    try:
        with open(BENCH_PATH, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
    except Exception:
        return []
    return rows


def snapshot() -> dict:
    rows = read_rows()
    by_version: dict[str, list[dict]] = {}
    for r in rows:
        by_version.setdefault(r.get("version", "v0"), []).append(r)

    curve = []
    for ver in sorted(by_version):
        group = by_version[ver]
        n = len(group) or 1
        curve.append({
            "version": ver, "runs": len(group),
            "avg_evidence": round(sum(r["evidence_count"] for r in group) / n, 1),
            "avg_domains": round(sum(r["independent_domains"] for r in group) / n, 1),
            "unsupported_ratio": round(sum(r["unsupported_ratio"] for r in group) / n, 3),
            "bound_ratio": round(sum(r.get("bound_ratio", 0) for r in group) / n, 3),
            "avg_credibility": round(sum(r.get("avg_credibility", 0) for r in group) / n, 1),
            "first_pass_rate": round(sum(1 for r in group if r["gate_pass_first_try"]) / n, 3),
            "avg_seconds": round(sum(r["elapsed_ms"] for r in group) / n / 1000, 1),
        })

    latest: dict[str, dict] = {}
    for r in rows:
        cid = r.get("case_id")
        if cid and (cid not in latest or r["ts"] > latest[cid]["ts"]):
            latest[cid] = r

    return {"cases": CASES, "curve": curve, "rows": rows[-120:],
            "latest": latest, "total_runs": len(rows)}
