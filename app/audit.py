# -*- coding: utf-8 -*-
"""质检层：规则先行，LLM 补充。

双层设计的理由：规则可复现、可解释、永远可用；LLM 能看出规则看不到的
「这个维度压根没提」。LLM 挂了就回落规则分，不会让质检整体失效。
"""
from __future__ import annotations

import json
import re

from .models import Claim, Evidence, Issue, Quality, root_domain

# 规则阈值
MIN_EVIDENCE = 4
MIN_DOMAINS = 3
MIN_BOUND_RATIO = 0.6
MIN_DIMENSIONS = 3
REQUIRED_SECTIONS = ("核心结论", "关键证据", "红队", "行动")


def _dimension_coverage(markdown: str, dimensions: list[str], want: int) -> float:
    got = len([d for d in dimensions if d])
    if not got:
        got = len(re.findall(r"^##\s+\S", markdown or "", flags=re.M))
    return round(min(1.0, got / max(1, want)), 3)


def run_rules(*, markdown: str, claims: list[Claim], evidence: list[Evidence],
              dimensions: list[str], want_dimensions: int, rounds: int = 0) -> Quality:
    """纯规则质检。这一层的每个结论都能当场手算复现。"""
    sourced = [e for e in evidence if e.fetch_status == "sourced"]
    # 按主域去重：一篇稿子被网易几个子域转载三次，仍然只算一个独立来源
    domains = {root_domain(e.domain) for e in sourced if e.domain}
    unsupported = [c for c in claims if not c.evidence_ids]
    cross = [c for c in claims if c.cross_validated]
    n_claims = len(claims) or 1

    q = Quality(
        evidence_count=len(sourced),
        claim_count=len(claims),
        unsupported_claims=len(unsupported),
        independent_domains=len(domains),
        cross_validated_ratio=round(len(cross) / n_claims, 3),
        evidence_bound_ratio=round((len(claims) - len(unsupported)) / n_claims, 3),
        dimension_coverage=_dimension_coverage(markdown, dimensions, want_dimensions),
        avg_credibility=round(sum(e.credibility for e in sourced) / len(sourced), 1) if sourced else 0.0,
        rounds=rounds,
    )

    issues: list[Issue] = []
    if q.evidence_count < MIN_EVIDENCE:
        issues.append(Issue(target="evidence", severity="high", raised_by="rules",
                            reason=f"已核验证据仅 {q.evidence_count} 条，低于下限 {MIN_EVIDENCE}"))
    if q.independent_domains < MIN_DOMAINS:
        issues.append(Issue(target="entity:来源多样性", severity="high", raised_by="rules",
                            reason=f"独立来源域名仅 {q.independent_domains} 个，低于下限 {MIN_DOMAINS}，"
                                   f"存在单一信源风险"))
    for c in unsupported[:6]:
        issues.append(Issue(target=f"claim:{c.text[:16]}", severity="medium", raised_by="rules",
                            reason="该论点未绑定任何证据"))
    if q.dimension_coverage < 0.7:
        issues.append(Issue(target=f"dimension:覆盖度", severity="medium", raised_by="rules",
                            reason=f"维度覆盖 {int(q.dimension_coverage * 100)}%，"
                                   f"低于要求的 {want_dimensions} 个角度"))
    missing = [s for s in REQUIRED_SECTIONS if s not in (markdown or "")]
    if missing:
        issues.append(Issue(target="schema", severity="medium", raised_by="rules",
                            reason=f"报告缺少必备章节：{'、'.join(missing)}"))

    q.issues = [i.to_dict() for i in issues]
    q.scores = {
        "evidence_sufficiency": min(100, int(q.evidence_count / MIN_EVIDENCE * 70) +
                                    min(30, q.independent_domains * 8)),
        "dimension_completeness": int(q.dimension_coverage * 100),
        "conclusion_confidence": int(q.evidence_bound_ratio * 100),
        "structure_integrity": 100 - len(missing) * 22,
        "cross_validation": int(q.cross_validated_ratio * 100),
    }
    high = [i for i in issues if i.severity == "high"]
    q.verdict = "rework" if (high or q.evidence_bound_ratio < MIN_BOUND_RATIO) else "pass"
    return q


def merge_llm_review(q: Quality, raw: str) -> Quality:
    """把 LLM 五维评审并进来。解析失败就保持规则结论。"""
    if not raw:
        return q
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return q
    try:
        j = json.loads(m.group(0))
    except Exception:
        return q
    scores = j.get("scores") or {}
    for k, v in scores.items():
        try:
            rule_v = q.scores.get(k)
            q.scores[k] = int((int(v) + rule_v) / 2) if rule_v is not None else int(v)
        except Exception:
            continue
    for it in (j.get("issues") or [])[:8]:
        if isinstance(it, dict) and it.get("reason"):
            q.issues.append(Issue(target=str(it.get("target", ""))[:60],
                                  severity=str(it.get("severity", "medium")),
                                  reason=str(it.get("reason"))[:200],
                                  raised_by="auditor").to_dict())
    q.review = str(j.get("review", ""))[:300]
    if j.get("verdict") == "rework":
        q.verdict = "rework"
    # 只有「再跑一轮真能补上」的维度低分才判返工。
    # 交叉验证率取决于世上到底存不存在第二个独立来源——打回去重写并不能
    # 把来源变出来，硬卡这一项只会白烧一轮算力，然后仍然不达标。
    # 它低于 60 时记一条问题留在报告里，但不作为返工触发条件。
    reworkable = ("evidence_sufficiency", "dimension_completeness",
                  "conclusion_confidence", "structure_integrity")
    if any(int(q.scores.get(k, 100)) < 60 for k in reworkable):
        q.verdict = "rework"
    if int(q.scores.get("cross_validation", 100)) < 60:
        q.issues.append(Issue(
            target="dimension:交叉验证", severity="low", raised_by="rules",
            reason=f"交叉验证率仅 {q.scores.get('cross_validation')}%，"
                   f"多数论点只有单一独立来源。这受限于公开可检索到的来源数量，"
                   f"不作返工处理，但请据此调低对这份报告的信任度。").to_dict())
    return q


def meets_hard_bar(q: Quality) -> bool:
    """硬指标是否达标。

    这条线全部由代码算，不看质检官的主观评语：证据得有量、来源得散开、
    维度得覆盖到、可返工的评分维不低于 60。达到了就允许出报告，
    质检官剩下的意见以「保留意见」的形式附在报告里，而不是无限期否决——
    否则只要模型愿意一直挑刺，任何报告都永远出不来。

    未绑证论点允许少量存在：模型有时会把「诚实缺口」也写成论点
    （例如「该公司未披露 ARR，无法评估」），这类句子本就不该绑证据。
    只要整体绑证率仍高，就不该永久卡死出报告。
    """
    reworkable = ("evidence_sufficiency", "dimension_completeness",
                  "conclusion_confidence", "structure_integrity")
    if q.claim_count <= 0:
        unbound_ok = False
    elif q.unsupported_claims == 0:
        unbound_ok = True
    else:
        ratio = q.unsupported_claims / q.claim_count
        bound = q.evidence_bound_ratio if q.evidence_bound_ratio else (
            1.0 - ratio)
        unbound_ok = ratio <= 0.3 and bound >= 0.7
    return (unbound_ok
            and q.evidence_count >= 6
            and q.independent_domains >= 4
            and q.dimension_coverage >= 0.7
            and all(int(q.scores.get(k, 0)) >= 60 for k in reworkable))


def route_rework(q: Quality) -> tuple[str | None, list[dict]]:
    """决定返工回哪一步。这是 Envelope 路由的判定逻辑。

      entity: / evidence  -> 回博学补采（缺的是料）
      dimension: / schema -> 回慎思重推（料够但没想清楚）
      claim:              -> 缺证据回博学，其余回慎思
    """
    if q.verdict != "rework":
        return None, []
    to_boxue, to_shensi = [], []
    for it in q.issues:
        target = str(it.get("target", ""))
        if target.startswith("entity:") or target == "evidence" or target.startswith("claim:"):
            to_boxue.append(it)
        else:
            to_shensi.append(it)
    if to_boxue:
        return "boxue", to_boxue + to_shensi
    if to_shensi:
        return "shensi", to_shensi
    # LLM 判了 rework 但规则没给出具体 issue，合成一条回慎思
    return "shensi", [{"target": "dimension:整体", "severity": "medium",
                       "raised_by": "auditor", "reason": q.review or "质检官判定需要重新推理"}]


def gate_summary(q: Quality, before: Quality | None = None) -> dict:
    """给前端门禁条用的数据。有前后对比时把差值也算出来。"""
    out = {"verdict": q.verdict, "headline": q.headline(), "scores": q.scores,
           "rounds": q.rounds, "issues": q.issues[:8]}
    if before:
        out["delta"] = {
            "evidence_count": q.evidence_count - before.evidence_count,
            "independent_domains": q.independent_domains - before.independent_domains,
            "unsupported_claims": q.unsupported_claims - before.unsupported_claims,
            "evidence_bound_ratio": round(q.evidence_bound_ratio - before.evidence_bound_ratio, 3),
        }
        out["before"] = {"evidence_count": before.evidence_count,
                         "independent_domains": before.independent_domains,
                         "unsupported_claims": before.unsupported_claims,
                         "headline": before.headline()}
    return out
