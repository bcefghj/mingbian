# -*- coding: utf-8 -*-
"""页面核验取证器。

模型给出的引用链接不能照单全收——这里真的去访问一遍：
拿到标题与正文摘录才算 sourced，拿不到就降级或标 retrieval_failed。
这是「无证据不立论」在网络层的落实。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone, timedelta

import httpx

from ..models import Evidence, domain_of
from .. import credibility

CST = timezone(timedelta(hours=8))
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 搜狗 / 百度的跳转页是 JS 重定向，httpx 的 follow_redirects 跟不到真实站点。
# 不解开它，所有证据的域名都会变成 sogou.com，独立来源数就成了假数字。
_JS_HOP = re.compile(
    r'(?:window\.location\.replace|window\.location\.href\s*=|URL\s*=|location\.replace)'
    r'\s*\(?\s*["\']([^"\']{12,})["\']', re.I)

_TAG = re.compile(r"<(script|style|nav|footer|header)[\s\S]*?</\1>", re.I)
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_DATE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")


def _now() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")


def _text_of(html: str) -> str:
    body = _TAG.sub(" ", html)
    body = _ANY_TAG.sub(" ", body)
    body = body.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    body = _WS.sub(" ", body)
    return "\n".join(ln.strip() for ln in body.split("\n") if ln.strip())


def _title_of(html: str) -> str:
    m = re.search(r"<title[^>]*>([\s\S]{0,200}?)</title>", html, re.I)
    return _WS.sub(" ", m.group(1)).strip() if m else ""


def _published_of(html: str) -> str:
    for pat in (r'property=["\']article:published_time["\'][^>]*content=["\']([^"\']+)',
                r'name=["\']pubdate["\'][^>]*content=["\']([^"\']+)',
                r'name=["\']publishdate["\'][^>]*content=["\']([^"\']+)'):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)[:10]
    m = _DATE.search(html)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


async def verify_url(client: httpx.AsyncClient, url: str, *,
                     title_hint: str = "", collected_by: str = "",
                     source_type: str = "") -> Evidence:
    """访问一个 URL，返回带真实 fetch_status 的 Evidence。"""
    ev = Evidence(url=url, title=title_hint or "", captured_at=_now(),
                  collected_by=collected_by, source_type=source_type or "unknown")
    if not url.startswith("http"):
        ev.fetch_status = "retrieval_failed"
        ev.excerpt = "链接格式无效，未能访问"
        return credibility.apply(ev)
    try:
        r = await client.get(url, headers=UA, follow_redirects=True, timeout=9.0)
        if r.status_code >= 400:
            ev.fetch_status = "retrieval_failed"
            ev.excerpt = f"访问返回 HTTP {r.status_code}，未取到正文"
            ev.degraded = True
            return credibility.apply(ev)
        html = r.text[:400_000]

        # 跳转页：解开 JS 重定向，拿到真实站点
        hop = 0
        while hop < 2 and len(html) < 12_000:
            m = _JS_HOP.search(html)
            if not m or not m.group(1).startswith("http"):
                break
            hop += 1
            r = await client.get(m.group(1), headers=UA, follow_redirects=True, timeout=9.0)
            if r.status_code >= 400:
                break
            html = r.text[:400_000]

        ev.url = str(r.url)
        ev.domain = domain_of(ev.url)
        ev.title = _title_of(html) or ev.title or ev.domain
        ev.published_at = _published_of(html)
        text = _text_of(html)
        if len(text) < 120:
            ev.degraded = True
            ev.excerpt = (text or "页面正文过短或为动态渲染，仅确认链接可达")[:300]
        else:
            ev.excerpt = text[:420]
        ev.fetch_status = "sourced"
    except Exception as e:
        ev.fetch_status = "retrieval_failed"
        ev.degraded = True
        ev.excerpt = f"访问失败：{type(e).__name__}"
    return credibility.apply(ev)


async def verify_evidence(items: list[Evidence], *, limit: int = 16) -> int:
    """就地核验一批已有 Evidence：模型说它引用了这个链接，我们真的去打开看看。

    就地更新是必须的——重新建对象会让论点上已经绑好的 ev_id 全部失效。
    返回核验通过的条数。
    """
    todo = [e for e in items if e.url.startswith("http")][:limit]
    if not todo:
        return 0

    async with httpx.AsyncClient(timeout=12.0) as client:
        async def one(ev: Evidence):
            fresh = await verify_url(client, ev.url, title_hint=ev.title,
                                     collected_by=ev.collected_by,
                                     source_type=ev.source_type)
            ev.url = fresh.url
            ev.domain = fresh.domain
            ev.fetch_status = fresh.fetch_status
            ev.degraded = fresh.degraded
            if fresh.fetch_status == "sourced":
                # 模型给的摘录往往比页面开头更贴题，两边都留一点
                if fresh.title and len(fresh.title) > len(ev.title or ""):
                    ev.title = fresh.title
                if not ev.excerpt:
                    ev.excerpt = fresh.excerpt
                if not ev.published_at:
                    ev.published_at = fresh.published_at
            else:
                ev.excerpt = (ev.excerpt or "") + f"（链接核验：{fresh.excerpt}）"
            ev.source_type = "unknown"    # 域名可能变了，让打分重新推断
            credibility.apply(ev)
        await asyncio.gather(*[one(e) for e in todo], return_exceptions=True)

    for e in items:
        if e.fetch_status == "pending":
            e.fetch_status = "not_searched"
            credibility.apply(e)
    return sum(1 for e in todo if e.fetch_status == "sourced")


async def verify_many(hits: list[dict], *, limit: int = 10) -> list[Evidence]:
    """并发核验一批检索结果。

    检索器（尤其博查）已经给了发布时间与长摘要，这些信息比我们从 HTML 里
    抠出来的开头几百字更贴题，所以页面抓取只用来补充，不覆盖已有的好数据。
    """
    hits = hits[:limit]
    if not hits:
        return []
    async with httpx.AsyncClient(timeout=12.0) as client:
        tasks = [verify_url(client, h.get("url", ""), title_hint=h.get("title", ""),
                            collected_by=h.get("collected_by", ""),
                            source_type=h.get("source_type", ""))
                 for h in hits]
        done = await asyncio.gather(*tasks, return_exceptions=True)

    out: list[Evidence] = []
    for hit, ev in zip(hits, done):
        if not isinstance(ev, Evidence):
            continue
        # 检索引擎给的发布时间比页面正则抠出来的靠谱得多——
        # 页面里第一个像日期的字符串经常是导航栏或版权年份
        pub = hit.get("published_at") or ""
        if pub:
            ev.published_at = pub
        snip = hit.get("snippet") or ""
        if snip and len(snip) > len(ev.excerpt or ""):
            ev.excerpt = snip[:900]
        if hit.get("title") and len(hit["title"]) > len(ev.title or ""):
            ev.title = hit["title"]
        if hit.get("relevance") is not None:
            ev.value = f"相关度 {hit['relevance']}"
        credibility.apply(ev)
        out.append(ev)
    return out
