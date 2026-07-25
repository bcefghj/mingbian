# -*- coding: utf-8 -*-
"""立场演变轨迹。

一份研判最容易被质疑的地方是：结论到底是「查完之后得出的」，
还是「一开始就想好了、后面只是找材料凑」。

轨迹层专治这个。它在流水线的每个关键节点打一个点，记下当时的
立场、概率、论点数与证据结构，并且把两点之间的差算出来、写清楚
是哪一步动作导致了变化。看完这条线，就能回答：

  - 引擎最初的先验是什么，取证之后有没有被推翻
  - 质检打回那一轮，究竟改善了什么
  - 辩论有没有真的动摇结论，还是只走了个过场

所有数字都来自运行时的真实状态，不做任何事后美化。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict

from .models import Claim, Evidence, ipcc_term, root_domain

# 阶段展示名。key 与 pipeline 里的节点保持一致，方便回放页对齐。
STAGE_CN = {
    "intake": "意图漏斗",
    "collect": "博学 · 取证",
    "analyze": "慎思 · 初判",
    "audit": "质检 · 门禁",
    "rework": "返工 · 重判",
    "debate": "明辨 · 辩论",
    "final": "笃行 · 定稿",
}

# 立场的方向性。用来判断「掉头」还是「加固」。
_BULL = ("看多", "可行", "值得", "支持")
_BEAR = ("看空", "不可行", "高风险", "反对", "骗局")


def _polarity(stance: str) -> int:
    s = stance or ""
    if any(w in s for w in _BULL):
        return 1
    if any(w in s for w in _BEAR):
        return -1
    return 0


@dataclass
class StancePoint:
    """轨迹上的一个点。字段全部是当时的实测值。"""
    seq: int = 0
    stage: str = ""
    stage_cn: str = ""
    title: str = ""                 # 这一步做了什么
    stance: str = ""
    polarity: int = 0
    probability: float | None = None
    interval: list[float] | None = None
    ipcc: str = ""
    claims_total: int = 0
    claims_supported: int = 0
    claims_strong: int = 0
    evidence_count: int = 0
    independent_domains: int = 0
    trigger: str = ""               # 是什么动作把状态推到这一点
    shift: str = ""                 # 与上一点相比发生了什么（代码生成，非模型撰写）
    shift_kind: str = "init"        # init | hold | firm | soften | reverse | ground
    delta_probability: float | None = None
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class StanceTracker:
    """按时间顺序收点，并在收点时立刻算出与上一点的差异。"""

    def __init__(self):
        self.points: list[StancePoint] = []

    # ---------------------------------------------------------- 打点
    def mark(self, stage: str, *, title: str, trigger: str = "",
             stance: str = "", probability: float | None = None,
             interval: list[float] | None = None,
             claims: list[Claim] | None = None,
             evidence: list[Evidence] | None = None) -> StancePoint:
        claims = claims or []
        sourced = [e for e in (evidence or []) if e.fetch_status == "sourced"]
        p = StancePoint(
            seq=len(self.points) + 1,
            stage=stage,
            stage_cn=STAGE_CN.get(stage, stage),
            title=title,
            stance=stance,
            polarity=_polarity(stance),
            probability=probability,
            interval=interval,
            ipcc=ipcc_term(probability) if probability is not None else "",
            claims_total=len(claims),
            claims_supported=sum(1 for c in claims if c.evidence_ids),
            claims_strong=sum(1 for c in claims if c.strength in ("strong", "moderate")),
            evidence_count=len(sourced),
            independent_domains=len({root_domain(e.domain) for e in sourced if e.domain}),
            trigger=trigger,
        )
        prev = self.points[-1] if self.points else None
        p.shift_kind, p.shift, p.delta_probability = _diff(prev, p)
        self.points.append(p)
        return p

    # ---------------------------------------------------------- 导出
    def to_list(self) -> list[dict]:
        return [p.to_dict() for p in self.points]

    def summary(self) -> dict:
        """给报告页顶部用的一句话概括 + 供图表用的极值。"""
        if not self.points:
            return {}
        probs = [p.probability for p in self.points if p.probability is not None]
        first_stated = next((p for p in self.points if p.stance), None)
        last = self.points[-1]
        reversals = [p for p in self.points if p.shift_kind == "reverse"]
        firmed = [p for p in self.points if p.shift_kind == "firm"]

        moved = [p for p in self.points if p.shift_kind in ("firm", "soften")]
        drift = None
        if first_stated and last.probability is not None and \
                first_stated.probability is not None:
            drift = last.probability - first_stated.probability

        if reversals:
            head = (f"结论在「{reversals[-1].stage_cn}」这一步发生过方向性掉头，"
                    f"最终落在「{last.stance or '未表态'}」。")
        elif moved:
            who = "、".join(dict.fromkeys(p.stage_cn for p in moved))
            direction = "上调" if (drift or 0) > 0 else "下调"
            amount = f"共{direction} {abs(drift) * 100:.0f} 个百分点" if drift else "把握程度被修正"
            head = (f"方向自始至终没变，但「{who}」动过把握程度，{amount}。"
                    f"这说明后续环节不是走过场。")
        elif drift and abs(drift) >= 0.03:
            head = (f"方向没变，证据补强过程中把握程度小幅"
                    f"{'上调' if drift > 0 else '下调'} {abs(drift) * 100:.0f} 个百分点。")
        elif firmed:
            head = "方向没变，后续步骤主要在补强证据、收窄区间。"
        else:
            head = ("研判全程立场稳定，方向与把握程度都没有被后续环节推翻——"
                    "初判在证据、质检与门控三关之后依然成立。")

        return {
            "headline": head,
            "points": len(self.points),
            "reversals": len(reversals),
            "prob_min": min(probs) if probs else None,
            "prob_max": max(probs) if probs else None,
            "final_stance": last.stance,
            "final_probability": last.probability,
            "evidence_growth": (self.points[0].evidence_count, last.evidence_count),
        }


def _diff(prev: StancePoint | None, cur: StancePoint) -> tuple[str, str, float | None]:
    """两点之间发生了什么。这段刻意只用算术，不让模型来讲故事。"""
    if prev is None:
        return "init", "研判起点：尚未取证，不持任何立场。", None

    dp = None
    if prev.probability is not None and cur.probability is not None:
        dp = round(cur.probability - prev.probability, 3)

    bits: list[str] = []
    d_ev = cur.evidence_count - prev.evidence_count
    d_dom = cur.independent_domains - prev.independent_domains
    d_sup = cur.claims_supported - prev.claims_supported
    if d_ev:
        bits.append(f"证据 {prev.evidence_count}→{cur.evidence_count}")
    if d_dom:
        bits.append(f"独立来源 {prev.independent_domains}→{cur.independent_domains}")
    if d_sup:
        bits.append(f"绑定证据的论点 {prev.claims_supported}→{cur.claims_supported}")
    if dp:
        bits.append(f"概率 {prev.probability * 100:.0f}%→{cur.probability * 100:.0f}%")

    # 先判方向，再判强弱
    if prev.polarity and cur.polarity and prev.polarity != cur.polarity:
        kind = "reverse"
        lead = f"方向掉头：由「{prev.stance}」改判为「{cur.stance}」。"
    elif not prev.stance and cur.stance:
        kind = "ground"
        lead = f"首次形成立场：「{cur.stance}」。"
    elif dp is not None and dp >= 0.05:
        kind = "firm"
        lead = "结论被进一步加固。"
    elif dp is not None and dp <= -0.05:
        kind = "soften"
        lead = "结论被削弱，把握程度下调。"
    elif d_ev > 0 or d_dom > 0:
        kind = "ground"
        lead = "方向未变，证据底座变厚。"
    else:
        kind = "hold"
        lead = "本步未改变结论。"

    return kind, (lead + ("（" + "、".join(bits) + "）" if bits else "")), dp
