# -*- coding: utf-8 -*-
"""专家团：16 位可派遣专家，分决策 / 分析 / 取证三层。

每位专家是一个可独立触发的取证 Skill：绑定各自偏好的权威源，
派遣时给出「为什么是这几位」的理由，跑完统计调用次数与产出。
"""
from __future__ import annotations

EXPERT_ROSTER = [
    # ---- L3 决策层：永远在场 ----
    {"key": "chief", "name": "首席研判官", "layer": "决策",
     "role": "统合各方证据，裁定最终结论与整体置信度，对边界与不确定性负责",
     "sources": ["全部专家的结构化产出"]},
    {"key": "auditor", "name": "质检官", "layer": "决策",
     "role": "检查证据覆盖、独立来源数、论点绑定率与结构完整度，不达标就打回重做",
     "sources": ["证据池", "论点集"]},
    {"key": "contra", "name": "红队官", "layer": "决策",
     "role": "专职证伪：主动找反例、挑其他专家的漏洞，绝不为了达成共识而妥协",
     "sources": ["反向检索", "少数派观点", "历史打脸案例"]},

    # ---- L2 分析层 ----
    {"key": "macro", "name": "宏观周期专家", "layer": "分析",
     "role": "利率曲线、信贷与 GDP、央行政策、领先指标，判断当前处在周期什么位置",
     "sources": ["国家统计局", "中国人民银行", "IMF", "BIS"]},
    {"key": "market", "name": "市场定价专家", "layer": "分析",
     "role": "只看交易数据：价格、成交量、持仓、期权隐含波动率，用价格反推市场共识",
     "sources": ["新浪财经", "东方财富", "交易所公告"]},
    {"key": "industry", "name": "行业竞争专家", "layer": "分析",
     "role": "行业格局、龙头动向、上下游订单、产能与估值，判断真实景气与拐点",
     "sources": ["行业协会", "券商研报", "上市公司披露"]},
    {"key": "policy", "name": "政策法规专家", "layer": "分析",
     "role": "现行法规、监管口径、地方细则与执行力度，判断政策对结论的硬约束",
     "sources": ["中国政府网", "各部委通告", "地方政府公报"]},
    {"key": "finance", "name": "财务审计专家", "layer": "分析",
     "role": "财报勾稽、现金流质量、关联交易与商誉，识别账面粉饰",
     "sources": ["巨潮资讯", "年报问询函", "审计意见"]},
    {"key": "tech", "name": "技术可行性专家", "layer": "分析",
     "role": "技术路线成熟度、工程门槛与替代方案，判断「能不能做成」",
     "sources": ["论文与专利", "开源社区", "厂商白皮书"]},

    # ---- L1 取证层 ----
    {"key": "sentiment", "name": "舆情情报专家", "layer": "取证",
     "role": "多平台舆情聚类与情绪走向，识别水军与一致性操纵",
     "sources": ["微博", "知乎", "小红书", "贴吧"]},
    {"key": "entity", "name": "关联溯源专家", "layer": "取证",
     "role": "抽取人 / 机构 / 账号 / 产品实体，发现隐藏关系与团伙式一致性",
     "sources": ["工商登记", "股权穿透", "账号指纹"]},
    {"key": "judicial", "name": "司法风控专家", "layer": "取证",
     "role": "涉诉记录、行政处罚、失信被执行与刑事风险",
     "sources": ["中国裁判文书网", "信用中国", "证监会处罚"]},
    {"key": "price", "name": "价格追踪专家", "layer": "取证",
     "role": "历史价格曲线、跨平台比价与促销周期，判断当前是不是买点",
     "sources": ["电商历史价", "行情接口", "招投标公示"]},
    {"key": "community", "name": "社区口碑专家", "layer": "取证",
     "role": "真实用户长评、离职员工反馈、投诉平台记录，抓一手体感",
     "sources": ["脉脉", "黑猫投诉", "垂直论坛"]},
    {"key": "official", "name": "官方公告专家", "layer": "取证",
     "role": "只认一手公告：备案、资质、招股书、监管批复",
     "sources": ["中基协", "国家企业信用信息公示系统", "交易所披露"]},
    {"key": "timeline", "name": "时间线还原专家", "layer": "取证",
     "role": "把散落的事件按时间排序，还原事情怎么一步步走到今天",
     "sources": ["新闻存档", "公告序列", "网页快照"]},
]

_BY_KEY = {e["key"]: e for e in EXPERT_ROSTER}
ALWAYS_ON = ["chief", "auditor", "contra"]

# 关键词 -> (领域名, 优先专家)
_RULES: list[tuple[tuple[str, ...], str, list[str]]] = [
    (("房价", "楼市", "买房", "房租", "地产", "学区"), "楼市",
     ["macro", "market", "policy", "sentiment", "timeline"]),
    (("黄金", "金价", "白银", "避险"), "贵金属",
     ["market", "macro", "price", "sentiment"]),
    (("比特币", "btc", "加密", "以太", "eth", "虚拟货币"), "加密资产",
     ["market", "policy", "sentiment", "judicial"]),
    (("ai", "人工智能", "泡沫", "芯片", "算力", "大模型"), "科技产业",
     ["industry", "tech", "market", "macro"]),
    (("入职", "offer", "跳槽", "这家公司", "尽调", "面试"), "公司尽调",
     ["finance", "judicial", "community", "industry", "official"]),
    (("骗局", "诈骗", "稳赚", "日返", "传销", "杀猪", "非法集资", "返利"), "反诈",
     ["judicial", "entity", "official", "sentiment", "community"]),
    (("装修", "报价", "合同", "施工", "建材"), "消费维权",
     ["price", "judicial", "community", "policy"]),
    (("留学", "考研", "考公", "专业", "就业率"), "教育决策",
     ["macro", "industry", "community", "policy"]),
    (("开店", "加盟", "创业", "副业", "生意"), "创业可行性",
     ["industry", "finance", "policy", "community", "price"]),
    (("养生", "保健", "偏方", "有害", "致癌", "传闻"), "健康传闻",
     ["official", "judicial", "sentiment", "timeline"]),
]

DEFAULT_KEYS = ["macro", "market", "industry", "sentiment", "entity"]


def route(question: str) -> tuple[str, list[str]]:
    """返回 (领域名, 命中的关键词组)。"""
    q = (question or "").lower()
    for words, domain, _ in _RULES:
        hit = [w for w in words if w.lower() in q]
        if hit:
            return domain, hit
    return "通用研判", []


def pick_experts(question: str, limit: int = 8) -> list[dict]:
    """按问题动态派遣。决策层三位永远在场，其余按领域挑。"""
    q = (question or "").lower()
    picked: list[str] = []
    for words, _domain, keys in _RULES:
        if any(w.lower() in q for w in words):
            picked = list(keys)
            break
    if not picked:
        picked = list(DEFAULT_KEYS)

    ordered = ALWAYS_ON + [k for k in picked if k not in ALWAYS_ON]
    out, seen = [], set()
    for k in ordered:
        if k in _BY_KEY and k not in seen:
            seen.add(k)
            out.append(_BY_KEY[k])
        if len(out) >= limit:
            break
    return out


def dispatch_reason(question: str, experts: list[dict]) -> str:
    """生成「为什么是这几位」，让派遣可解释而不是黑箱。"""
    domain, hits = route(question)
    field_names = "、".join(e["name"] for e in experts if e["key"] not in ALWAYS_ON)
    if hits:
        kw = "、".join(hits[:3])
        return (f"问题命中「{kw}」，判定为{domain}类。除常驻的首席研判官、质检官、红队官外，"
                f"追加派遣 {field_names}。")
    return (f"问题未命中特定领域关键词，按{domain}默认组队。除常驻三位外，"
            f"追加派遣 {field_names}。")


def get(key: str) -> dict | None:
    return _BY_KEY.get(key)


def roster_public() -> list[dict]:
    return EXPERT_ROSTER
