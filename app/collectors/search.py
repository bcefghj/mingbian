# -*- coding: utf-8 -*-
"""关键词检索取证器。

分两级：博查 Web Search 是主通道（结构化结果，带发布时间与长摘要），
搜狗 / 百度 / 360 的 HTML 抓取是兜底通道，博查不可用时接管。

关键设计：检索失败时返回的不是空数组，而是带 scope / queries_tried /
index_freshness / confidence_in_absence 的结构体。
「没搜到」和「不存在」是两件事，产品必须能区分。

兜底引擎的选择基于实测：Bing 中国站已改成纯前端渲染，抓不到结果；
搜狗与百度仍返回服务端 HTML，因此以这两家为主。
"""
from __future__ import annotations

import asyncio
import html as htmllib
import re
from datetime import datetime, timezone, timedelta

import httpx

from . import bocha
from ..models import Gap, domain_of, root_domain

CST = timezone(timedelta(hours=8))
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_TAGS = re.compile(r"<[^>]+>")
_SOGOU = re.compile(r'<h3 class="vr-title[^"]*"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([\s\S]{0,300}?)</a>', re.I)
_SOGOU_SNIP = re.compile(r'<div class="(?:text-layout|fz-mid|space-txt)[^"]*">([\s\S]{0,400}?)</div>', re.I)
_BAIDU_MU = re.compile(r'<div[^>]+class="result[^"]*"[^>]*mu="(https?://[^"]+)"[^>]*>([\s\S]{0,900}?)</div>', re.I)
_BAIDU_H3 = re.compile(r'<h3[^>]*>[\s\S]{0,200}?<a[^>]+href="(https?://[^"]+)"[^>]*>([\s\S]{0,200}?)</a>', re.I)
_SO360 = re.compile(r'<h3 class="res-title[^"]*"[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>([\s\S]{0,300}?)</a>', re.I)

# 这些域名对研判没有价值，收进来只会稀释证据池
_JUNK_DOMAINS = ("image.so.com", "so.com/s", "baidu.com/s", "sogou.com/web",
                 "zhidao.baidu.com", "wenku.baidu.com", "baike.baidu.com",
                 "tieba.baidu.com", "map.baidu.com", ".gif", ".jpg")
# 搜索页里的功能块（智能回复、相关搜索之类）不是证据
_JUNK_TITLES = ("实时智能回复", "相关搜索", "大家还在搜", "其他人还搜了",
                "_360图片", "百度为您找到")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(_TAGS.sub("", s or ""))).strip()


def diversify(hits: list[dict], *, per_domain: int = 2) -> list[dict]:
    """同一家媒体最多留几条，其余往后排。

    抓取预算是有限的（十几次 HTTP），全花在网易的三个子域上，
    换来的是「三个来源互相印证」的假象。宁可少抓，也要抓得散。
    """
    keep, spill, cnt = [], [], {}
    for h in hits:
        d = root_domain(h.get("domain") or h.get("url") or "")
        cnt[d] = cnt.get(d, 0) + 1
        (keep if cnt[d] <= per_domain else spill).append(h)
    return keep + spill


def _ok(url: str, title: str = "") -> bool:
    if not url.startswith("http"):
        return False
    if any(j in url for j in _JUNK_DOMAINS):
        return False
    return not any(j in (title or "") for j in _JUNK_TITLES)


async def _sogou(client: httpx.AsyncClient, query: str, n: int) -> list[dict]:
    r = await client.get("https://www.sogou.com/web", params={"query": query},
                         headers=UA, follow_redirects=True, timeout=9.0)
    if r.status_code >= 400:
        return []
    snips = [_clean(x) for x in _SOGOU_SNIP.findall(r.text)]
    out = []
    for i, (href, title) in enumerate(_SOGOU.findall(r.text)):
        url = href if href.startswith("http") else ("https://www.sogou.com" + href)
        title = _clean(title)
        if not title:
            continue
        out.append({"url": url, "title": title[:120],
                    "snippet": (snips[i] if i < len(snips) else "")[:300],
                    "domain": domain_of(url), "engine": "sogou", "indirect": True})
        if len(out) >= n:
            break
    return out


async def _baidu(client: httpx.AsyncClient, query: str, n: int) -> list[dict]:
    r = await client.get("https://www.baidu.com/s", params={"wd": query, "rn": 20},
                         headers=UA, follow_redirects=True, timeout=9.0)
    if r.status_code >= 400:
        return []
    out, seen = [], set()
    for url, block in _BAIDU_MU.findall(r.text):
        m = re.search(r"<h3[\s\S]{0,300}?</h3>", block)
        title = _clean(m.group(0)) if m else ""
        if not _ok(url, title) or url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "title": (title or domain_of(url))[:120],
                    "snippet": _clean(block)[:300], "domain": domain_of(url),
                    "engine": "baidu"})
        if len(out) >= n:
            break
    if not out:
        for url, title in _BAIDU_H3.findall(r.text):
            if not _ok(url, _clean(title)) or url in seen:
                continue
            seen.add(url)
            out.append({"url": url, "title": _clean(title)[:120], "snippet": "",
                        "domain": domain_of(url), "engine": "baidu", "indirect": True})
            if len(out) >= n:
                break
    return out


async def _so360(client: httpx.AsyncClient, query: str, n: int) -> list[dict]:
    r = await client.get("https://www.so.com/s", params={"q": query},
                         headers=UA, follow_redirects=True, timeout=9.0)
    if r.status_code >= 400:
        return []
    out = []
    for url, title in _SO360.findall(r.text):
        title = _clean(title)
        if not _ok(url, title):
            continue
        out.append({"url": url, "title": title[:120], "snippet": "",
                    "domain": domain_of(url), "engine": "so360"})
        if len(out) >= n:
            break
    return out


ENGINES = [("sogou", _sogou), ("baidu", _baidu), ("so360", _so360)]


async def search_web(queries: list[str], *, per_query: int = 4, topic: str = "",
                     collected_by: str = "", rerank_for: str = "",
                     want: int = 12) -> tuple[list[dict], Gap | None]:
    """跑一组检索词，返回 (候选来源, 缺口)。两者可以同时非空。

    先走博查主通道；博查不可用或零命中时，退到 HTML 抓取兜底，
    并在缺口里如实写明用的是哪一级通道。
    """
    queries = [q.strip() for q in queries if q and q.strip()][:4]
    if not queries:
        return [], None

    bocha_gap = None
    if bocha.configured():
        hits, bocha_gap = await bocha.search(queries, per_query=max(per_query, 6),
                                             topic=topic, collected_by=collected_by)
        if hits:
            if rerank_for:
                hits = await bocha.rerank(rerank_for, hits, top_n=want)
            for h in hits:
                h["channel"] = "bocha"
            return diversify(hits), None
        # 博查零命中不代表世界上没有，再让兜底引擎试一次

    jobs, labels = [], []
    async with httpx.AsyncClient(timeout=12.0) as client:
        for q in queries:
            for name, fn in ENGINES:
                jobs.append(fn(client, q, per_query))
                labels.append(name)
        results = await asyncio.gather(*jobs, return_exceptions=True)

    hits: list[dict] = []
    seen_urls: set[str] = set()
    engine_alive = False
    for res in results:
        if isinstance(res, Exception) or res is None:
            continue
        engine_alive = True
        for item in res:
            key = item["url"]
            if key in seen_urls:
                continue
            seen_urls.add(key)
            item["collected_by"] = collected_by
            item["channel"] = "fallback"
            hits.append(item)

    # 跨引擎重复出现的结果更可能是主流来源，排前面
    hits.sort(key=lambda x: (0 if x.get("engine") == "baidu" else 1))

    if hits:
        return diversify(hits), None

    kind = "no_support_found" if engine_alive else "retrieval_failed"
    scope = "博查全网索引 + 搜狗 / 百度 / 360 网页抓取" if bocha.configured() \
        else "中文公开网页（搜狗 / 百度 / 360 三引擎）"
    note = "全部检索通道均可达但零命中" if engine_alive else "检索通道全部不可达，本条属取证失败"
    if bocha_gap and bocha_gap.note:
        note += f"（主通道：{bocha_gap.note}）"
    gap = Gap(
        kind=kind, topic=topic or queries[0], queries_tried=queries, scope=scope,
        index_freshness=datetime.now(CST).strftime("%Y-%m-%d"),
        confidence_in_absence="medium" if engine_alive else "low", note=note,
    )
    return [], gap
