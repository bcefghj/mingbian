# -*- coding: utf-8 -*-
"""指标计算。每个指标都配一句人话定义，直接展示在仪表盘上。

指标口径必须公开——不写清楚算法的效率倍数是营销数字，不是工程指标。
"""
from __future__ import annotations

from .models import Claim, Evidence, Quality, root_domain
from . import store

MANUAL_MINUTES_PER_SOURCE = 8   # 人工核一个信源的经验耗时
BASELINE_DOMAINS = 6            # 人工研判的基线独立来源数

DEFINITIONS = {
    "efficiency": {
        "name": "效率倍数",
        "formula": "人工估时（独立来源数 × 8 分钟）÷ 实际耗时",
        "why": "假设一位分析师核一个信源平均 8 分钟。这不是精确测量，是可复核的估算口径。",
    },
    "coverage": {
        "name": "覆盖倍数",
        "formula": "本次独立域名数 ÷ 基线 6",
        "why": "基线取人工研判常见的 6 个来源。低于 1 说明我们没比人工查得更广。",
    },
    "consistency": {
        "name": "一致性",
        "formula": "0.5 × 论点绑证据占比 + 0.5 × Schema 完整度",
        "why": "衡量报告内部是否自洽：说了话有没有据，该有的结构有没有。",
    },
    "accuracy": {
        "name": "高置信占比",
        "formula": "strong 与 moderate 论点数 ÷ 总论点数",
        "why": "注意这不是「正确率」——正确率要等事实揭晓才能算，我们不冒充。",
    },
    "correction": {
        "name": "人工修正率",
        "formula": "被人工标注为存疑或驳回的论点 ÷ 已复核论点",
        "why": "越低说明机器判断越靠得住。这个数字由用户在复核队列里投票产生。",
    },
}


def compute(*, evidence: list[Evidence], claims: list[Claim], quality: Quality,
            elapsed_ms: int, report_id: str = "") -> dict:
    sourced = [e for e in evidence if e.fetch_status == "sourced"]
    domains = {root_domain(e.domain) for e in sourced if e.domain}
    n_dom = len(domains) or 1
    minutes = n_dom * MANUAL_MINUTES_PER_SOURCE
    actual_min = max(0.2, elapsed_ms / 60000)
    high = [c for c in claims if c.strength in ("strong", "moderate")]
    n_claims = len(claims) or 1

    reviews = store.get_reviews(report_id) if report_id else {}
    flagged = [v for v in reviews.values() if v.get("verdict") in ("doubted", "rejected")]

    return {
        "efficiency": {
            "value": round(minutes / actual_min, 1), "unit": "×",
            "detail": f"人工估时 {minutes} 分钟 ÷ 实际 {actual_min:.1f} 分钟",
            **DEFINITIONS["efficiency"],
        },
        "coverage": {
            "value": round(n_dom / BASELINE_DOMAINS, 2), "unit": "×",
            "detail": f"{n_dom} 个独立域名 ÷ 基线 {BASELINE_DOMAINS}",
            **DEFINITIONS["coverage"],
        },
        "consistency": {
            "value": round(0.5 * quality.evidence_bound_ratio +
                           0.5 * (quality.scores.get("structure_integrity", 0) / 100), 3),
            "unit": "", "detail": f"绑证据 {int(quality.evidence_bound_ratio * 100)}% · "
                                  f"结构 {quality.scores.get('structure_integrity', 0)}%",
            **DEFINITIONS["consistency"],
        },
        "accuracy": {
            "value": round(len(high) / n_claims, 3), "unit": "",
            "detail": f"{len(high)} / {len(claims)} 条论点达到中等及以上强度",
            **DEFINITIONS["accuracy"],
        },
        "correction": {
            "value": round(len(flagged) / len(reviews), 3) if reviews else 0.0,
            "unit": "", "detail": f"{len(flagged)} 条被标存疑或驳回 / {len(reviews)} 条已复核"
                                  if reviews else "暂无人工复核记录",
            **DEFINITIONS["correction"],
        },
        "raw": {
            "evidence_count": len(sourced), "independent_domains": n_dom,
            "claim_count": len(claims), "elapsed_ms": elapsed_ms,
            "avg_credibility": quality.avg_credibility,
            "rework_rounds": quality.rounds,
        },
    }


def global_snapshot() -> dict:
    """全站累计快照，仪表盘顶部用。"""
    reports = store.list_reports(200)
    ledger = store.ledger_stats()
    rv = store.review_stats()
    return {"reports": len(reports), "ledger": ledger, "reviews": rv,
            "definitions": DEFINITIONS}
