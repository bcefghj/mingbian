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
            "definitions": DEFINITIONS, "corpus": corpus_snapshot(),
            "triggers": pending_triggers()}


def pending_triggers(limit: int = 12) -> list[dict]:
    """所有报告里写明的「什么情况下要回来复看」，汇总成一张待观测清单。

    每份报告结尾都写了触发条件，但写完就散在各自的报告里，没人会挨份翻。
    结论是有保质期的：把这些条件集中摆出来，才谈得上「持续研判」而不是一次性问答。
    """
    out = []
    for d in _all_documents():
        for t in (d.get("triggers") or [])[:2]:
            t = str(t).strip()
            if not t:
                continue
            out.append({"text": t[:160], "report_id": d.get("id", ""),
                        "question": (d.get("question") or "")[:34],
                        "tag": d.get("tag") or (d.get("plan") or {}).get("domain") or "",
                        "created_at": d.get("created_at") or 0})
    out.sort(key=lambda x: -x["created_at"])
    return out[:limit]


def _all_documents() -> list[dict]:
    """已落库的全部研判：预置示例 + 用户跑出来的报告，按 id 去重。"""
    from . import demos  # 避免 import 环

    docs: list[dict] = []
    seen: set[str] = set()
    for did in [d["id"] for d in demos.list_demos()]:
        d = demos.get_demo(did)
        if d:
            docs.append(d)
            seen.add(did)
    for row in store.list_reports(200):
        rid = row.get("id")
        if rid in seen:
            continue
        d = store.get_report(rid)
        if d:
            docs.append(d)
            seen.add(rid)
    return docs


def expert_usage() -> dict:
    """每位专家的真实出场记录。

    专家册如果只是十六张静态卡片，那它就只是提示词里的一串称谓。
    这里把「被派遣过几次、跑出多少证据、在哪些领域出场、说过什么」
    从已落库的报告里重新数一遍——数不出来的能力就是不存在的能力。
    """
    docs = _all_documents()
    stats: dict[str, dict] = {}
    for d in docs:
        tag = d.get("tag") or (d.get("plan") or {}).get("domain") or ""
        for e in d.get("experts") or []:
            if not isinstance(e, dict) or not e.get("key"):
                continue
            row = stats.setdefault(e["key"], {
                "dispatched": 0, "calls": 0, "spans": 0, "evidence": 0,
                "collected": 0, "findings_count": 0, "domains": [], "findings": [],
            })
            row["dispatched"] += 1
            row["calls"] += int(e.get("calls") or 0)
            row["spans"] += int(e.get("spans") or e.get("calls") or 0)
            row["evidence"] += int(e.get("evidence") or 0)
            row["collected"] += int(e.get("collected") or 0)
            if tag and tag not in row["domains"]:
                row["domains"].append(tag)
            f = (e.get("finding") or "").strip()
            if f:
                row["findings_count"] += 1
                # 卡片上只摆得下三条，但计数要按全量走，否则「结论入册」会被截断成 3
                if len(row["findings"]) < 3:
                    row["findings"].append({"text": f[:160], "report_id": d.get("id", ""),
                                            "question": (d.get("question") or "")[:40]})
    return {"runs": len(docs), "experts": stats}


def corpus_snapshot() -> dict:
    """把已落库的全部研判（示例 + 用户跑的）汇总成一组可核对的数字。

    首页那条统计带和仪表盘共用这一份。刻意逐份读文件重算而不是维护计数器：
    数字对不上时，删掉一份报告再刷新就能自证——计数器做不到这一点。
    """
    docs = _all_documents()

    ev_total = cred_sum = cred_n = 0
    claims_total = claims_bound = claims_strong = 0
    rework_rounds = reworked_docs = 0
    debate_gated = debate_held = debate_rounds = calls_saved = 0
    reversals = 0
    domains: dict[str, int] = {}
    tags: dict[str, int] = {}
    gaps_total = tensions_total = redteam_total = 0

    for d in docs:
        evs = d.get("evidence")
        if isinstance(evs, list):
            for e in evs:
                if not isinstance(e, dict) or e.get("fetch_status") != "sourced":
                    continue
                ev_total += 1
                dom = root_domain(e.get("domain") or "")
                if dom:
                    domains[dom] = domains.get(dom, 0) + 1
                if e.get("credibility"):
                    cred_sum += e["credibility"]
                    cred_n += 1

        cls = d.get("claims")
        if isinstance(cls, list):
            claims_total += len(cls)
            claims_bound += sum(1 for c in cls if isinstance(c, dict) and c.get("evidence_ids"))
            claims_strong += sum(1 for c in cls if isinstance(c, dict)
                                 and c.get("strength") in ("strong", "moderate"))

        q = d.get("quality") or {}
        if q.get("rounds"):
            rework_rounds += q["rounds"]
            reworked_docs += 1

        deb = d.get("debate") or {}
        if deb.get("gate"):
            debate_gated += 1
            calls_saved += deb["gate"].get("calls_saved") or 0
            if deb.get("held"):
                debate_held += 1
                debate_rounds += len(deb.get("rounds") or [])

        for p in d.get("trajectory") or []:
            if isinstance(p, dict) and p.get("shift_kind") == "reverse":
                reversals += 1

        for key, box in (("gaps", "g"), ("tensions", "t"), ("redteam", "r")):
            v = d.get(key)
            if isinstance(v, list):
                if key == "gaps":
                    gaps_total += len(v)
                elif key == "tensions":
                    tensions_total += len(v)
                else:
                    redteam_total += len(v)

        tag = d.get("tag") or (d.get("plan") or {}).get("domain") or ""
        if tag:
            tags[tag] = tags.get(tag, 0) + 1

    n = len(docs) or 1
    top_domains = sorted(domains.items(), key=lambda kv: -kv[1])[:12]
    return {
        "documents": len(docs),
        "evidence_total": ev_total,
        "evidence_avg": round(ev_total / n, 1),
        "independent_domains": len(domains),
        "avg_credibility": round(cred_sum / cred_n, 1) if cred_n else 0.0,
        "claims_total": claims_total,
        "claims_bound": claims_bound,
        "bound_ratio": round(claims_bound / claims_total, 3) if claims_total else 0.0,
        "strong_ratio": round(claims_strong / claims_total, 3) if claims_total else 0.0,
        "rework_rounds": rework_rounds,
        "rework_ratio": round(reworked_docs / n, 3),
        "debate_gated": debate_gated,
        "debate_held": debate_held,
        "debate_rounds": debate_rounds,
        "debate_open_ratio": round(debate_held / debate_gated, 3) if debate_gated else 0.0,
        "calls_saved": calls_saved,
        "stance_reversals": reversals,
        "gaps_total": gaps_total,
        "tensions_total": tensions_total,
        "redteam_total": redteam_total,
        "top_domains": [{"domain": k, "count": v} for k, v in top_domains],
        "tags": [{"tag": k, "count": v} for k, v in
                 sorted(tags.items(), key=lambda kv: -kv[1])],
    }
