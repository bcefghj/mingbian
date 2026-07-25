# -*- coding: utf-8 -*-
"""博查 Web Search 取证器（主检索通道）。

选它的理由很实际：自己爬搜索引擎 HTML 拿到的东西没有发布时间、没有正文摘要，
而来源可信度打分里「有没有发布日期」「时效多久」「摘录够不够长」都是加减分项。
博查直接给结构化字段，取证质量和打分精度一起上来了。

同时提供语义排序（Semantic Rerank）：检索回来一堆结果，
按与问题的语义相关度重排后再取前 N 条，避免用关键词碰巧命中的噪声撑场面。
"""
from __future__ import annotations

import asyncio
import os

import httpx

from ..models import Gap, domain_of

BASE = os.getenv("BOCHA_BASE_URL", "https://api.bocha.cn/v1").rstrip("/")
KEY = os.getenv("BOCHA_API_KEY", "")
RERANK_MODEL = os.getenv("BOCHA_RERANK_MODEL", "gte-rerank")
TIMEOUT = float(os.getenv("BOCHA_TIMEOUT", "20"))


def configured() -> bool:
    return bool(KEY)


def _h() -> dict:
    return {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def _norm_date(s: str) -> str:
    """2026-06-09T04:52:07+08:00 -> 2026-06-09"""
    return (s or "")[:10]


async def _one(client: httpx.AsyncClient, query: str, count: int,
               freshness: str) -> list[dict]:
    r = await client.post(f"{BASE}/web-search", headers=_h(), timeout=TIMEOUT,
                          json={"query": query, "summary": True,
                                "freshness": freshness, "count": count})
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}")
    j = r.json()
    if str(j.get("code")) not in ("200", "0"):
        raise RuntimeError(str(j.get("msg") or j.get("code"))[:80])
    pages = (((j.get("data") or {}).get("webPages") or {}).get("value") or [])
    out = []
    for p in pages:
        url = (p.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        out.append({
            "url": url,
            "title": (p.get("name") or "")[:140],
            # summary 是干净长摘要，snippet 只有 100 字，能拿长的就拿长的
            "snippet": (p.get("summary") or p.get("snippet") or "")[:900],
            "domain": domain_of(url),
            "site_name": (p.get("siteName") or "")[:40],
            "published_at": _norm_date(p.get("datePublished") or p.get("dateLastCrawled") or ""),
            "engine": "bocha",
            "query": query,
        })
    return out


async def search(queries: list[str], *, per_query: int = 8,
                 freshness: str = "noLimit", topic: str = "",
                 collected_by: str = "") -> tuple[list[dict], Gap | None]:
    """并发跑一组检索词。返回 (候选来源, 缺口)。"""
    queries = [q.strip() for q in queries if q and q.strip()][:5]
    if not queries:
        return [], None
    if not KEY:
        return [], Gap(kind="not_searched", topic=topic or queries[0],
                       queries_tried=queries, scope="博查全网检索",
                       confidence_in_absence="low",
                       note="BOCHA_API_KEY 未配置，本轮未走主检索通道")

    async with httpx.AsyncClient(timeout=TIMEOUT + 5) as client:
        results = await asyncio.gather(
            *[_one(client, q, per_query, freshness) for q in queries],
            return_exceptions=True)

    hits: list[dict] = []
    seen: set[str] = set()
    alive, errs = False, []
    for res in results:
        if isinstance(res, Exception):
            errs.append(str(res)[:60])
            continue
        alive = True
        for item in res:
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            item["collected_by"] = collected_by
            hits.append(item)

    if hits:
        return hits, None
    gap = Gap(
        kind="no_support_found" if alive else "retrieval_failed",
        topic=topic or queries[0], queries_tried=queries,
        scope="博查全网检索（近百亿网页索引）",
        confidence_in_absence="high" if alive else "low",
        note="检索通道正常但零命中" if alive else f"博查接口异常：{'；'.join(errs[:2])}",
    )
    return [], gap


async def rerank(query: str, hits: list[dict], *, top_n: int = 10) -> list[dict]:
    """按与问题的语义相关度重排。失败就原样返回，不影响主流程。"""
    if not KEY or len(hits) <= top_n:
        return hits
    docs = [(h.get("snippet") or h.get("title") or "")[:1200] for h in hits]
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{BASE}/rerank", headers=_h(),
                                  json={"model": RERANK_MODEL, "query": query,
                                        "top_n": min(top_n, len(docs)),
                                        "return_documents": False,
                                        "documents": docs})
        if r.status_code >= 400:
            return hits[:top_n]
        rows = ((r.json().get("data") or {}).get("results") or [])
        picked, used = [], set()
        for row in rows:
            i = row.get("index")
            if isinstance(i, int) and 0 <= i < len(hits) and i not in used:
                used.add(i)
                h = dict(hits[i])
                h["relevance"] = round(float(row.get("relevance_score") or 0), 4)
                picked.append(h)
        # rerank 只返回 top_n，剩下的按原顺序补齐，别把结果丢了
        picked += [h for i, h in enumerate(hits) if i not in used]
        return picked[:max(top_n, len(picked))]
    except Exception:
        return hits[:top_n]


def status() -> dict:
    """检索通道状态，台账页展示用。

    不去 ping 余额接口：那要真花一次配额，而页面每刷新一次就花一次，
    为了一个状态灯烧检索额度不划算。
    """
    return {
        "provider": "bocha" if KEY else "html_fallback",
        "label": "博查 Web Search" if KEY else "搜狗 / 百度 / 360 抓取",
        "configured": bool(KEY),
        "note": "全网索引，返回结构化摘要与发布时间" if KEY
                else "未配置 BOCHA_API_KEY，走 HTML 抓取兜底，取不到发布时间",
    }
