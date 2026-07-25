# -*- coding: utf-8 -*-
"""选择性辩论门控 + 结构化对抗辩论。

## 为什么要「选择性」

多智能体辩论很贵：每一轮都是两次长上下文推理，深研档一轮就要两三分钟。
而绝大多数问题在证据摊开之后并没有真正的分歧——「日返 1.5% 是不是骗局」
不需要辩，证据一边倒。这时候强行开辩，两个 Agent 只会互相客套一轮，
烧掉 token 又不改变任何结论，还会让用户以为「辩过了所以更可靠」。

所以这里先算一个门控分：只有真的出现分歧信号才开辩。

## 门控关掉时也要留痕

「这次没辩」本身是一条结论，必须写清楚判据。默默跳过和主动说明
「六项信号只命中一项，分歧不足以支撑一轮辩论」是两回事——后者才
是可审计的工程行为。

## 辩论怎么组织

正方不额外调模型：**当前报告本身就是正方陈述**，再让它复述一遍纯属浪费。
每轮两次调用：

  1. 红队官（contra）发起攻击，必须落到可证伪的点上
  2. 质检官（auditor）作为裁判，裁定攻击是否成立、结论要不要改

裁判有权调整立场与概率，调整幅度受限（单轮最多 ±0.2），
避免一轮辩论把结论掀翻——那通常意味着模型在迎合最后一个说话的人。
"""
from __future__ import annotations

import json
import re

from .models import Claim, Evidence, Tension, root_domain

# 每档给多少轮辩论额度。速判档不辩：它连 LLM 质检都跳过。
ROUNDS_BY_MODE = {"quick": 0, "deep": 1, "expert": 2}
MODE_CN = {"quick": "速判", "deep": "深研", "expert": "专家"}

# 门控阈值。低于它就不辩，并把分数一起写进报告。
OPEN_THRESHOLD = 2.0

# 裁判单轮最多能挪动多少概率
MAX_JUDGE_SHIFT = 0.2

# 高风险领域：判错的代价不对称，宁可多辩一轮
HIGH_STAKES = ("反诈", "公司尽调", "健康传闻", "加密资产", "消费维权")


# 门控口径公开在方法论页。权重不写出来，「选择性」就成了黑箱。
SIGNAL_SPEC = [
    {"key": "contested", "name": "论点存在对立证据", "weight": 1.2,
     "rule": "存在 strength=contested 的论点（同一论点同时挂着支持与反对证据）"},
    {"key": "tension", "name": "存在未解张力", "weight": 1.0,
     "rule": "mb-meta.tensions 非空，即模型自己承认两派谈不拢"},
    {"key": "gray_zone", "name": "置信度落在灰区", "weight": 1.0,
     "rule": "最终概率落在 35%–65% 之间"},
    {"key": "mismatch", "name": "立场与概率不自洽", "weight": 1.4,
     "rule": "给出了方向性立场，但该立场成立概率 < 50%"},
    {"key": "thin_sources", "name": "证据来源集中", "weight": 0.8,
     "rule": "独立主域 < 5 个，或检出一致性团伙"},
    {"key": "weak_claims", "name": "弱论点占比偏高", "weight": 0.8,
     "rule": "strength 为 weak 或 unsupported 的论点占比 ≥ 34%"},
]

SPEC = {
    "signals": SIGNAL_SPEC,
    "threshold": OPEN_THRESHOLD,
    "high_stakes_bonus": 0.6,
    "high_stakes_domains": list(HIGH_STAKES),
    "rounds_by_mode": ROUNDS_BY_MODE,
    "max_judge_shift": MAX_JUDGE_SHIFT,
    "note": "门控分 = 命中信号的权重之和。达到阈值才开辩；"
            "高风险领域（判错代价不对称）阈值下调 0.6。"
            "裁判单轮最多挪动 20 个百分点，防止一轮辩论掀翻结论。",
}


def evaluate(*, claims: list[Claim], tensions: list[Tension],
             evidence: list[Evidence], confidence: dict,
             stance: str, domain: str, cliques: list[dict],
             mode: str) -> dict:
    """算门控分。六个信号，每个都能当着评委的面手算复现。"""
    sourced = [e for e in evidence if e.fetch_status == "sourced"]
    domains = {root_domain(e.domain) for e in sourced if e.domain}
    n_claims = len(claims) or 1
    p = confidence.get("probability")

    signals: list[dict] = []

    def sig(key, name, hit, weight, detail):
        signals.append({"key": key, "name": name, "hit": bool(hit),
                        "weight": weight if hit else 0.0, "detail": detail})

    # 1. 论点层面已经打架：有论点同时挂着正反证据
    contested = [c for c in claims if c.strength == "contested"]
    sig("contested", "论点存在对立证据", contested, 1.2,
        f"{len(contested)} 条论点同时挂着支持与反对证据"
        if contested else "没有论点同时挂着正反证据")

    # 2. 未解张力：模型自己都承认两派谈不拢
    sig("tension", "存在未解张力", tensions, 1.0,
        f"{len(tensions)} 组张力未能调和" if tensions else "未识别出未解张力")

    # 3. 概率落在灰区：这种时候「差不多」的判断最不可信
    gray = p is not None and 0.35 <= p <= 0.65
    sig("gray_zone", "置信度落在灰区", gray, 1.0,
        f"最终概率 {p * 100:.0f}%，处于 35–65% 的模棱两可区间" if gray
        else (f"最终概率 {p * 100:.0f}%，方向明确" if p is not None else "本次未量化概率"))

    # 4. 立场与概率不自洽：给了明确方向，算出来的把握却不到一半。
    #    这是最该辩的一种情况——报告在用肯定句陈述一件更可能是错的事。
    directional = bool(stance) and "中性" not in stance and "均等" not in stance
    mismatch = directional and p is not None and p < 0.5
    sig("mismatch", "立场与概率不自洽", mismatch, 1.4,
        f"报告给出明确立场「{stance}」，但成立概率只有 {p * 100:.0f}%，"
        f"等于在用肯定句陈述一件更可能不成立的事" if mismatch
        else ("立场与把握程度自洽" if directional else "本次未给出方向性立场"))

    # 5. 证据结构薄：来源太集中，或检出一致性团伙
    thin = len(domains) < 5 or bool(cliques)
    sig("thin_sources", "证据来源集中", thin, 0.8,
        f"独立来源 {len(domains)} 个"
        + (f"，且检出 {len(cliques)} 组一致性团伙" if cliques else "")
        if thin else f"独立来源 {len(domains)} 个，分布够散")

    # 6. 弱论点占比高：说得多、撑得少
    weak = [c for c in claims if c.strength in ("weak", "unsupported")]
    weak_ratio = len(weak) / n_claims
    weak_hit = weak_ratio >= 0.34
    sig("weak_claims", "弱论点占比偏高", weak_hit, 0.8,
        f"{len(weak)}/{len(claims)} 条论点强度为弱或无证据（{weak_ratio * 100:.0f}%）")

    score = round(sum(s["weight"] for s in signals), 2)
    hits = [s for s in signals if s["hit"]]

    # 高风险领域降低开辩门槛：判错的代价不对称
    threshold = OPEN_THRESHOLD - (0.6 if domain in HIGH_STAKES else 0.0)
    budget = ROUNDS_BY_MODE.get(mode, 1)
    should_open = score >= threshold

    if not should_open:
        reason = (f"门控分 {score}（阈值 {threshold}），"
                  f"六项分歧信号命中 {len(hits)} 项，不足以支撑一轮辩论。"
                  f"证据一边倒时开辩只会消耗算力、不会改变结论，本次跳过。")
        state = "closed"
    elif budget <= 0:
        reason = (f"门控分 {score} 已达阈值 {threshold}，判定存在真实分歧，"
                  f"但本档（{MODE_CN.get(mode, mode)}）不配辩论额度。"
                  f"想看对抗过程请改用深研或专家档重跑。")
        state = "no_budget"
    else:
        reason = ("命中 " + "、".join(s["name"] for s in hits) +
                  f"，门控分 {score} ≥ 阈值 {threshold}，判定存在真实分歧，开辩 {budget} 轮。")
        state = "open"

    return {
        "state": state,
        "open": state == "open",
        "score": score,
        "threshold": threshold,
        "budget": budget if state == "open" else 0,
        "domain": domain,
        "signals": signals,
        "hit_count": len(hits),
        "reason": reason,
        # 关掉时省下的调用数，用来支撑「选择性」这三个字不是空话
        "calls_saved": (ROUNDS_BY_MODE.get(mode, 1) * 2) if state != "open" else 0,
    }


# ---------------------------------------------------------------- 辩论执行

def parse_attack(raw: str) -> dict:
    """解析红队攻击。拿不到 JSON 就退化成纯文本，不让整轮辩论失败。"""
    j = _json_block(raw)
    if not j:
        return {"points": [{"claim": "", "attack": (raw or "")[:400],
                            "falsifiable": "", "severity": "medium"}],
                "strongest": (raw or "")[:200], "parsed": False}
    pts = []
    for it in (j.get("points") or [])[:5]:
        if not isinstance(it, dict) or not it.get("attack"):
            continue
        pts.append({
            "claim": str(it.get("claim", ""))[:120],
            "attack": str(it.get("attack", ""))[:400],
            "falsifiable": str(it.get("falsifiable", ""))[:200],
            "severity": str(it.get("severity", "medium"))[:8],
        })
    return {"points": pts, "strongest": str(j.get("strongest", ""))[:300],
            "parsed": bool(pts)}


def parse_judgement(raw: str, *, stance_before: str,
                    prob_before: float | None) -> dict:
    """解析裁判裁决，并把概率调整夹在允许范围内。"""
    j = _json_block(raw) or {}
    rulings = []
    for it in (j.get("rulings") or [])[:5]:
        if not isinstance(it, dict):
            continue
        verdict = str(it.get("verdict", "partial"))[:12]
        if verdict not in ("upheld", "rejected", "partial"):
            verdict = "partial"
        rulings.append({
            "attack": str(it.get("attack", ""))[:200],
            "verdict": verdict,
            "reason": str(it.get("reason", ""))[:300],
        })

    stance_after = _clean_stance(j.get("stance_after"), stance_before)
    delta = j.get("probability_delta")
    try:
        delta = float(delta)
    except (TypeError, ValueError):
        delta = 0.0
    delta = max(-MAX_JUDGE_SHIFT, min(MAX_JUDGE_SHIFT, delta))
    clamped = abs(float(j.get("probability_delta") or 0)) > MAX_JUDGE_SHIFT \
        if isinstance(j.get("probability_delta"), (int, float, str)) else False

    prob_after = prob_before
    if prob_before is not None and delta:
        prob_after = round(max(0.0, min(1.0, prob_before + delta)), 3)

    upheld = [r for r in rulings if r["verdict"] == "upheld"]
    partial = [r for r in rulings if r["verdict"] == "partial"]
    if upheld:
        outcome = "attacker"
    elif partial:
        outcome = "split"
    else:
        outcome = "defender"

    return {
        "rulings": rulings,
        "outcome": outcome,
        "stance_before": stance_before,
        "stance_after": stance_after,
        "probability_before": prob_before,
        "probability_after": prob_after,
        "probability_delta": round(delta, 3),
        "delta_clamped": clamped,
        "summary": str(j.get("summary", ""))[:300],
        "concessions": [str(x)[:200] for x in (j.get("concessions") or [])][:4],
        "residual": [str(x)[:200] for x in (j.get("residual_disagreement") or [])][:4],
    }


# 立场是要显示在结论卡标签上的，只收这几个词。
# 裁判很爱写「看空（但论证严谨性需提升）」这种带补充说明的长句，
# 直接截断会得到「看空（但论证严谨性需提升」这样断在半截的标签。
_STANCE_VOCAB = ("看多", "看空", "中性", "高风险", "可行", "不可行")


def _clean_stance(raw, fallback: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return fallback
    if s in _STANCE_VOCAB:
        return s
    # 带补充说明的，取开头命中的那个词；补充说明本身会进 summary，不会丢
    for w in _STANCE_VOCAB:
        if s.startswith(w):
            return w
    return fallback if fallback else (s[:6] if len(s) <= 6 else fallback)


def round_headline(rd: dict) -> str:
    """一句话说清这一轮辩出了什么。给实时工作台和报告页共用。"""
    jg = rd.get("judgement") or {}
    n = len(rd.get("attack", {}).get("points") or [])
    outcome = {"attacker": "红队攻击成立", "split": "部分成立",
               "defender": "原结论守住"}.get(jg.get("outcome"), "未裁定")
    dp = jg.get("probability_delta") or 0
    tail = f"，概率{'上调' if dp > 0 else '下调'} {abs(dp) * 100:.0f} 个百分点" if dp else "，概率未变"
    return f"第 {rd.get('round')} 轮：{n} 条攻击，{outcome}{tail}"


def _json_block(raw: str) -> dict | None:
    if not raw:
        return None
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None
