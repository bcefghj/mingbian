# -*- coding: utf-8 -*-
"""按问题动态挑选专家（体验层：派遣动画；报告层：prompt 仍要求模型自选）。"""
from .prompts import EXPERT_ROSTER

_BY_KEY = {e["key"]: e for e in EXPERT_ROSTER}

# 问题关键词 → 优先专家
_RULES = [
    (("房价", "楼市", "买房", "房租", "地产"), ["macro", "market", "industry", "sentiment", "contra"]),
    (("黄金", "金价", "避险"), ["market", "macro", "sentiment", "contra"]),
    (("比特币", "btc", "加密", "以太"), ["market", "sentiment", "risk", "contra"]),
    (("ai", "泡沫", "科技", "芯片"), ["industry", "market", "sentiment", "macro", "contra"]),
    (("入职", "offer", "公司", "尽调", "跳槽"), ["industry", "risk", "entity", "sentiment", "contra"]),
    (("骗局", "诈骗", "稳赚", "日返", "传销", "杀猪"), ["risk", "entity", "sentiment", "contra"]),
]


def pick_experts(question: str, limit: int = 5):
    q = (question or "").lower()
    keys = []
    for words, ks in _RULES:
        if any(w.lower() in q for w in words):
            keys = list(ks)
            break
    if not keys:
        keys = ["market", "macro", "sentiment", "entity", "risk", "contra"]
    # 红队几乎总要在场
    if "contra" not in keys:
        keys.append("contra")
    out = []
    for k in keys:
        if k in _BY_KEY and k not in {e["key"] for e in out}:
            out.append(_BY_KEY[k])
        if len(out) >= limit:
            break
    return out
