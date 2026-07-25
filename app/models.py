# -*- coding: utf-8 -*-
"""明辨 MINGBIAN · 数据契约层。

这一层是整个系统的地基：论点强度、证据绑定、质检结论全部由**代码**判定，
不交给模型自评。模型只负责写文字，凡是会影响可信度的判断一律走确定性规则。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Literal
from urllib.parse import urlparse

# ---------------------------------------------------------------- 枚举与常量

# 证据的取证状态。「没搜到」不等于「不存在」，所以拆成五态而不是布尔。
GroundStatus = Literal[
    "sourced",           # 已取证
    "pending",           # 检索中
    "retrieval_failed",  # 检索失败，可重试
    "no_support_found",  # 检索过但未找到支持来源
    "not_searched",      # 未检索，结论基于模型先验
]

GROUND_LABEL = {
    "sourced": "已取证",
    "pending": "检索中",
    "retrieval_failed": "检索失败",
    "no_support_found": "未找到支持来源",
    "not_searched": "未检索",
}

# 论点强度。由 make_claim() 依据证据结构判定。
ClaimStrength = Literal["strong", "moderate", "weak", "contested", "unsupported"]

STRENGTH_LABEL = {
    "strong": "强",
    "moderate": "中等",
    "weak": "弱",
    "contested": "存在争议",
    "unsupported": "无证据",
}

# 非颜色线索：色盲用户与黑白打印同样可读
STRENGTH_GLYPH = {
    "strong": "●●●",
    "moderate": "●●○",
    "weak": "●○○",
    "contested": "⚡",
    "unsupported": "○○○",
}

SourceType = Literal[
    "statistics", "official", "finance_media", "industry_media",
    "research", "judicial", "community", "self_media", "unknown",
]

SOURCE_LABEL = {
    "statistics": "统计公报",
    "official": "官方通告",
    "finance_media": "主流财经",
    "industry_media": "行业媒体",
    "research": "研究报告",
    "judicial": "司法文书",
    "community": "社区讨论",
    "self_media": "自媒体",
    "unknown": "未分类",
}

# IPCC 可能性量表。中文里「可能」在不同人心中能差出 60 个百分点，
# 所以产品内所有模糊词都必须能对到这张表上。
IPCC_SCALE = [
    ("几乎确定", 0.99, 1.00),
    ("很可能", 0.90, 1.00),
    ("可能", 0.66, 1.00),
    ("大致均等", 0.33, 0.66),
    ("不太可能", 0.00, 0.33),
    ("很不可能", 0.00, 0.10),
]


def ipcc_term(p: float) -> str:
    """把概率映射到 IPCC 术语，取最窄的那一档。"""
    if p is None:
        return "未量化"
    best, best_span = "大致均等", 9.0
    for term, lo, hi in IPCC_SCALE:
        if lo <= p <= hi and (hi - lo) < best_span:
            best, best_span = term, hi - lo
    return best


def _nid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def domain_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


# 这些后缀本身不构成一级域名，得多往前吃一段
_MULTI_SUFFIX = ("com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
                 "co.uk", "co.jp", "com.hk", "com.tw")


def root_domain(url_or_host: str) -> str:
    """归一到可注册主域，用来数「独立来源」。

    news.qq.com / m.163.com / c.m.163.com 看着是三个域名，实际是同一家媒体
    的分发副本。按主机名去重会把「一篇稿子转载三次」算成三个独立来源，
    交叉验证的强度就成了假的。
    """
    host = url_or_host if "://" not in url_or_host else domain_of(url_or_host)
    host = (host or "").lower().strip(".")
    if not host or host.replace(".", "").isdigit():
        return host
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    tail2 = ".".join(parts[-2:])
    return ".".join(parts[-3:]) if tail2 in _MULTI_SUFFIX else tail2


# ---------------------------------------------------------------- 数据类


@dataclass
class Evidence:
    """一条可核验的证据。credibility 由 credibility.score() 算，不经模型。"""
    ev_id: str = field(default_factory=lambda: _nid("ev"))
    url: str = ""
    domain: str = ""
    source_type: str = "unknown"
    title: str = ""
    excerpt: str = ""
    captured_at: str = ""
    published_at: str = ""
    credibility: int = 0
    fetch_status: str = "sourced"
    collected_by: str = ""
    value: str = ""          # 结构化数值（行情类证据用）
    degraded: bool = False   # 仅拿到摘要 / 降级入库

    def __post_init__(self):
        if self.url and not self.domain:
            self.domain = domain_of(self.url)

    @property
    def label(self) -> str:
        """chip 上显示的名字：优先中文站点名，其次域名。"""
        return self.domain or self.title[:12] or "未知来源"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source_label"] = SOURCE_LABEL.get(self.source_type, "未分类")
        d["ground_label"] = GROUND_LABEL.get(self.fetch_status, self.fetch_status)
        return d


@dataclass
class Claim:
    """一条论点。strength 永远由 make_claim() 判定，模型给的强度一律忽略。"""
    claim_id: str = field(default_factory=lambda: _nid("cl"))
    text: str = ""
    section: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    counter_evidence_ids: list[str] = field(default_factory=list)
    stance: str = "中性"
    strength: str = "unsupported"
    cross_validated: bool = False
    independent_domains: int = 0
    base_rate: dict | None = None      # {value, basis, source}
    adjustments: list[dict] = field(default_factory=list)  # [{delta, reason}]
    probability: float | None = None
    interval: list[float] | None = None
    author: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["strength_label"] = STRENGTH_LABEL.get(self.strength, self.strength)
        d["strength_glyph"] = STRENGTH_GLYPH.get(self.strength, "")
        if self.probability is not None:
            d["ipcc"] = ipcc_term(self.probability)
        return d


@dataclass
class Envelope:
    """Agent 之间的结构化消息。返工就是往回发一个 REWORK 信封。"""
    msg_id: str = field(default_factory=lambda: _nid("msg"))
    sender: str = ""
    receiver: str = ""
    task_type: str = "PRODUCE"   # PRODUCE | REWORK | PASS
    payload: dict = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    trace_ref: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Issue:
    issue_id: str = field(default_factory=lambda: _nid("is"))
    target: str = ""       # claim:xxx / entity:xxx / dimension:xxx / schema
    severity: str = "medium"
    reason: str = ""
    raised_by: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Gap:
    """证据缺口。这是产品的一等公民，不是错误日志。"""
    gap_id: str = field(default_factory=lambda: _nid("gap"))
    kind: str = "no_support_found"
    topic: str = ""
    queries_tried: list[str] = field(default_factory=list)
    scope: str = ""
    index_freshness: str = ""
    confidence_in_absence: str = "low"
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind_label"] = GROUND_LABEL.get(self.kind, self.kind)
        d["statement"] = self.statement()
        return d

    def statement(self) -> str:
        """生成诚实的缺口陈述。禁止裸写「没有相关信息」。"""
        scope = self.scope or "公开可检索范围"
        tried = f"，已尝试 {len(self.queries_tried)} 组关键词" if self.queries_tried else ""
        if self.kind == "retrieval_failed":
            return f"在{scope}检索{self.topic}时接口未返回{tried}。这是取证失败，不是证据为否。"
        if self.kind == "not_searched":
            return f"本次未对{self.topic}执行检索{tried}，相关表述仅基于模型既有知识，请自行核实。"
        return (
            f"在{scope}内未检索到关于{self.topic}的直接证据{tried}。"
            "这不代表结论为否，只代表当前证据不足。"
        )


@dataclass
class Tension:
    """未解张力：两派证据打架且无法调和。这是产品最有价值的输出之一。"""
    tension_id: str = field(default_factory=lambda: _nid("tn"))
    topic: str = ""
    side_a: dict = field(default_factory=dict)  # {stance, quote, evidence_ids, holder}
    side_b: dict = field(default_factory=dict)
    summary: str = ""
    resolved: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TraceSpan:
    """全链路埋点。决策回放页按 seq 步进。"""
    span_id: str = field(default_factory=lambda: _nid("sp"))
    seq: int = 0
    agent_id: str = ""
    stage: str = ""
    purpose: str = ""
    model: str = ""
    prompt_chars: int = 0
    output_chars: int = 0
    latency_ms: int = 0
    decision: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Quality:
    """质检结论。verdict 决定要不要返工。"""
    evidence_count: int = 0
    claim_count: int = 0
    unsupported_claims: int = 0
    independent_domains: int = 0
    cross_validated_ratio: float = 0.0
    evidence_bound_ratio: float = 0.0
    dimension_coverage: float = 0.0
    avg_credibility: float = 0.0
    scores: dict = field(default_factory=dict)
    verdict: str = "pass"        # pass | rework
    rounds: int = 0
    issues: list[dict] = field(default_factory=list)
    review: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["headline"] = self.headline()
        return d

    def headline(self) -> str:
        if self.verdict == "rework":
            # 说出真正卡住的那一项。以前不管什么原因都写「N 条论点未绑定证据」，
            # 结果打回时经常显示「0 条未绑定证据，已回炉补采」，自相矛盾。
            why = []
            if self.unsupported_claims:
                why.append(f"{self.unsupported_claims} 条论点未绑定证据")
            if self.evidence_count < 4:
                why.append(f"已核验证据仅 {self.evidence_count} 条")
            if self.independent_domains < 3:
                why.append(f"独立来源仅 {self.independent_domains} 个")
            if self.dimension_coverage < 0.7:
                why.append(f"维度覆盖 {int(self.dimension_coverage * 100)}%")
            if not why:
                first = (self.issues[0].get("reason") if self.issues else "") or "质检官判定需要复核"
                why.append(str(first)[:40])
            return "质检未通过：" + "、".join(why[:3]) + "，已回炉"
        bits = [f"{self.claim_count} 条论点全部绑定证据"] if not self.unsupported_claims else \
               [f"{self.claim_count - self.unsupported_claims}/{self.claim_count} 条论点绑定证据"]
        bits.append(f"{self.independent_domains} 个独立来源")
        if self.rounds:
            bits.append(f"经 {self.rounds} 轮返工")
        return " · ".join(bits)


# ---------------------------------------------------------------- 确定性规则

MIN_INDEPENDENT_DOMAINS = 2


def make_claim(text: str, evidence: list[Evidence], *, section: str = "",
               author: str = "", stance: str = "中性",
               counter: list[Evidence] | None = None, **kw) -> Claim:
    """论点强度的唯一判定入口。

    规则刻意简单到可以当着评委的面手算：
      - 没有证据                -> unsupported
      - 有反向证据且双方都成立  -> contested
      - >= 2 个独立域名         -> strong（且 cross_validated）
      - >= 2 条同源证据         -> moderate
      - 仅 1 条                 -> weak
    """
    counter = counter or []
    ev_ids = [e.ev_id for e in evidence]
    domains = {root_domain(e.domain) for e in evidence if e.domain}
    n_dom = len(domains)

    if not evidence:
        strength = "unsupported"
    elif counter and len(counter) >= 1 and len(evidence) >= 1:
        strength = "contested"
    elif n_dom >= MIN_INDEPENDENT_DOMAINS:
        strength = "strong"
    elif len(evidence) >= 2:
        strength = "moderate"
    else:
        strength = "weak"

    return Claim(
        text=text,
        section=section,
        evidence_ids=ev_ids,
        counter_evidence_ids=[e.ev_id for e in counter],
        stance=stance,
        strength=strength,
        cross_validated=(strength == "strong"),
        independent_domains=n_dom,
        author=author,
        **kw,
    )


def bind_evidence_ids(raw_ids: Any, pool: dict[str, Evidence]) -> tuple[list[str], list[Issue]]:
    """白名单过滤：模型给的 evidence_id 不在本次证据池里就丢掉并记 Issue。

    这是「无证据不立论」真正落到校验层的地方——模型编一个 ev_x 出来是没用的。
    """
    if not isinstance(raw_ids, list):
        return [], []
    kept, issues = [], []
    for rid in raw_ids:
        rid = str(rid).strip()
        if rid in pool:
            kept.append(rid)
        elif rid:
            issues.append(Issue(
                target="schema", severity="high", raised_by="binder",
                reason=f"模型引用了不存在的证据 ID {rid}，已丢弃",
            ))
    return kept, issues


def resolve_probability(base_rate: dict | None, adjustments: list[dict]) -> tuple[float | None, list[float] | None]:
    """基准率 + 调整项 -> 最终概率与区间。让 68% 这个数字可审计。"""
    if not base_rate or base_rate.get("value") is None:
        return None, None
    try:
        p = float(base_rate["value"])
    except (TypeError, ValueError):
        return None, None
    for adj in adjustments or []:
        try:
            p += float(adj.get("delta") or 0)
        except (TypeError, ValueError):
            continue
    p = max(0.0, min(1.0, p))
    # 区间宽度随调整项数量收窄：调整依据越多，我们越有把握
    span = max(0.05, 0.18 - 0.02 * len(adjustments or []))
    return round(p, 3), [round(max(0.0, p - span), 3), round(min(1.0, p + span), 3)]
