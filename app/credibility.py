# -*- coding: utf-8 -*-
"""来源可信度打分：0-100，纯规则，不经 LLM。

刻意做成可以当着评委面手算的形式——每一分的来处都能指出来。
让模型给来源打分是不可复现的，也无法解释。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from .models import Evidence, domain_of

CST = timezone(timedelta(hours=8))

# 基础分：按来源类型
BASE_SCORE = {
    "statistics": 75,
    "official": 70,
    "research": 65,
    "judicial": 68,
    "finance_media": 60,
    "industry_media": 55,
    "community": 45,
    "self_media": 35,
    "unknown": 40,
}

# 权威域名加分名单（命中即 +10）
AUTHORITATIVE = {
    "stats.gov.cn", "pbc.gov.cn", "gov.cn", "mof.gov.cn", "csrc.gov.cn",
    "safe.gov.cn", "customs.gov.cn", "nbs.gov.cn", "court.gov.cn",
    "wenshu.court.gov.cn", "amac.org.cn", "cninfo.com.cn", "sse.com.cn",
    "szse.cn", "chinabond.com.cn", "imf.org", "worldbank.org", "bis.org",
    "federalreserve.gov", "treasury.gov", "oecd.org", "who.int",
    "eastmoney.com", "sina.com.cn", "cs.com.cn", "stcn.com", "yicai.com",
    "caixin.com", "jrj.com.cn", "cnstock.com", "21jingji.com",
}

# 低质聚合 / 内容农场（命中即 -5）
LOW_QUALITY = {
    "baijiahao.baidu.com", "zhuanlan.zhihu.com", "toutiao.com",
    "sohu.com", "163.com", "kuaibao.qq.com", "dayu.com",
}

# 域名 -> 来源类型的先验推断
DOMAIN_TYPE_HINTS = [
    (("stats.gov.cn", "nbs.gov.cn"), "statistics"),
    ((".gov.cn", "gov.cn", ".gov", "pbc.", "csrc.", "safe."), "official"),
    (("court.gov.cn", "wenshu."), "judicial"),
    (("eastmoney", "sina.com", "yicai", "caixin", "cs.com.cn", "stcn",
      "cnstock", "21jingji", "jrj.com"), "finance_media"),
    (("bis.org", "imf.org", "worldbank", "oecd"), "research"),
    (("zhihu.com", "douban.com", "tieba", "v2ex", "xiaohongshu",
      "weibo.com", "bilibili"), "community"),
    (("baijiahao", "toutiao", "sohu.com", "163.com"), "self_media"),
]


def infer_source_type(url: str, declared: str = "") -> str:
    """来源类型优先信域名，其次信模型声明。"""
    dom = domain_of(url)
    if dom:
        for needles, stype in DOMAIN_TYPE_HINTS:
            if any(n in dom for n in needles):
                return stype
    if declared in BASE_SCORE:
        return declared
    return "unknown"


def _days_since(published_at: str) -> int | None:
    if not published_at:
        return None
    text = str(published_at).strip()[:10].replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            dt = datetime.strptime(text[: len(fmt.replace("%Y", "0000").replace("%m", "00").replace("%d", "00"))], fmt)
            return max(0, (datetime.now() - dt).days)
        except ValueError:
            continue
    return None


def score(ev: Evidence) -> tuple[int, list[str]]:
    """返回 (0-100 分, 逐条计分说明)。说明会直接展示在 provenance 卡里。"""
    breakdown: list[str] = []

    base = BASE_SCORE.get(ev.source_type, 40)
    breakdown.append(f"来源类型基础分 +{base}")
    total = base

    dom = ev.domain or domain_of(ev.url)
    if dom:
        if any(dom == a or dom.endswith("." + a) for a in AUTHORITATIVE):
            total += 10
            breakdown.append("权威域名 +10")
        if any(lq in dom for lq in LOW_QUALITY):
            total -= 5
            breakdown.append("低质聚合站 −5")

    days = _days_since(ev.published_at)
    if ev.published_at:
        total += 6
        breakdown.append("标注了发布日期 +6")
    if days is not None:
        if days <= 365:
            total += 5
            breakdown.append("一年内 +5")
        elif days <= 730:
            total += 2
            breakdown.append("两年内 +2")
        elif days > 1095:
            total -= 5
            breakdown.append("超过三年 −5")

    if ev.fetch_status == "sourced" and not ev.degraded:
        total += 6
        breakdown.append("原文抓取成功 +6")
    if ev.degraded:
        total -= 8
        breakdown.append("仅拿到摘要 −8")
    if len(ev.excerpt or "") > 200:
        total += 3
        breakdown.append("摘录充分 +3")
    if ev.fetch_status in ("retrieval_failed", "no_support_found", "not_searched"):
        total = min(total, 20)
        breakdown.append("未取到实证，封顶 20")

    total = max(0, min(100, total))
    return total, breakdown


def apply(ev: Evidence) -> Evidence:
    """就地补全 source_type 与 credibility。"""
    if ev.source_type in ("", "unknown"):
        ev.source_type = infer_source_type(ev.url, ev.source_type)
    ev.credibility, _ = score(ev)
    return ev


def tier(value: int) -> str:
    if value >= 75:
        return "高"
    if value >= 55:
        return "中"
    return "低"
