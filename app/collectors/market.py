# -*- coding: utf-8 -*-
"""行情取证器：抓公开实时行情，产出确定性 Evidence。

单源失败只跳过自己，不影响别的源（降级阶梯第一层）。
源路由按问题走：问黄金不去抓比特币，避免无关噪声撑场面。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Awaitable, Callable

import httpx

from ..models import Evidence, Gap
from .. import credibility

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
}
CST = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")


def _fmt(p: float) -> str:
    if abs(p) >= 1000:
        return f"{p:,.2f}"
    return f"{p:.2f}" if abs(p) >= 10 else f"{p:.4f}"


# ---------------------------------------------------------------- 单源实现

async def _sina_hf(client: httpx.AsyncClient, code: str, label: str) -> dict | None:
    r = await client.get(f"https://hq.sinajs.cn/list={code}",
                         headers={**UA, "Referer": "https://finance.sina.com.cn"})
    if r.status_code >= 400:
        return None
    m = re.search(r'="([^"]*)"', r.text)
    if not m or not m.group(1):
        return None
    parts = m.group(1).split(",")
    if len(parts) < 14:
        return None
    try:
        price = float(parts[0])
        prev = float(parts[7]) if parts[7] else None
        high = float(parts[4]) if parts[4] else None
        low = float(parts[5]) if parts[5] else None
    except Exception:
        return None
    chg = ((price - prev) / prev * 100) if prev else None
    extra = f"日内 {low:.2f}-{high:.2f}" if (high and low) else ""
    return {"name": label, "price": price, "chg_pct": chg, "currency": "USD",
            "as_of": f"{parts[12]} {parts[6]} CST".strip(), "extra": extra,
            "url": f"https://finance.sina.com.cn/futures/quotes/{code[3:]}.shtml",
            "site": "新浪财经"}


async def _eastmoney_gold(client: httpx.AsyncClient) -> dict | None:
    r = await client.get("https://push2.eastmoney.com/api/qt/stock/get",
                         params={"secid": "101.GC00Y", "fields": "f43,f57,f58,f60"},
                         headers=UA)
    if r.status_code >= 400:
        return None
    d = (r.json() or {}).get("data") or {}
    raw, prev_raw = d.get("f43"), d.get("f60")
    if raw is None:
        return None
    price = float(raw) / 10.0 if float(raw) > 10000 else float(raw)
    prev = None
    if prev_raw is not None:
        prev = float(prev_raw) / 10.0 if float(prev_raw) > 10000 else float(prev_raw)
    return {"name": d.get("f58") or "COMEX黄金", "price": price,
            "chg_pct": ((price - prev) / prev * 100) if prev else None,
            "currency": "USD", "as_of": _now(), "extra": "",
            "url": "https://quote.eastmoney.com/globalfuture/GC00Y.html",
            "site": "东方财富"}


async def _usd_cny(client: httpx.AsyncClient) -> dict | None:
    r = await client.get("https://open.er-api.com/v6/latest/USD", headers=UA)
    if r.status_code >= 400:
        return None
    j = r.json()
    if j.get("result") != "success":
        return None
    cny = (j.get("rates") or {}).get("CNY")
    if cny is None:
        return None
    return {"name": "美元兑人民币", "price": float(cny), "chg_pct": None, "currency": "",
            "as_of": j.get("time_last_update_utc") or _now(), "extra": "",
            "url": "https://open.er-api.com/v6/latest/USD", "site": "er-api"}


async def _em_index(client: httpx.AsyncClient, secid: str, label: str) -> dict | None:
    r = await client.get("https://push2.eastmoney.com/api/qt/stock/get",
                         params={"secid": secid, "fields": "f43,f58,f60,f170"}, headers=UA)
    if r.status_code >= 400:
        return None
    d = (r.json() or {}).get("data") or {}
    raw = d.get("f43")
    if raw is None:
        return None
    price = float(raw) / 100.0
    prev = float(d.get("f60") or 0) / 100.0 or None
    return {"name": d.get("f58") or label, "price": price,
            "chg_pct": ((price - prev) / prev * 100) if prev else None,
            "currency": "点", "as_of": _now(), "extra": "",
            "url": f"https://quote.eastmoney.com/zs{secid.split('.')[-1]}.html",
            "site": "东方财富"}


async def _binance(client: httpx.AsyncClient, symbol: str, label: str) -> dict | None:
    r = await client.get("https://api.binance.com/api/v3/ticker/24hr",
                         params={"symbol": symbol}, headers=UA)
    if r.status_code >= 400:
        return None
    j = r.json()
    return {"name": label, "price": float(j["lastPrice"]),
            "chg_pct": float(j.get("priceChangePercent") or 0), "currency": "USDT",
            "as_of": _now(), "extra": f"24h 量 {float(j.get('volume') or 0):,.0f}",
            "url": f"https://www.binance.com/zh-CN/trade/{symbol}", "site": "Binance"}


async def _coingecko(client: httpx.AsyncClient) -> dict | None:
    r = await client.get("https://api.coingecko.com/api/v3/simple/price",
                         params={"ids": "bitcoin", "vs_currencies": "usd",
                                 "include_24hr_change": "true"}, headers=UA)
    if r.status_code >= 400:
        return None
    d = (r.json() or {}).get("bitcoin") or {}
    if not d.get("usd"):
        return None
    return {"name": "Bitcoin", "price": float(d["usd"]),
            "chg_pct": d.get("usd_24h_change"), "currency": "USD", "as_of": _now(),
            "extra": "", "url": "https://www.coingecko.com/zh/coins/bitcoin",
            "site": "CoinGecko"}


async def _yahoo(client: httpx.AsyncClient, symbol: str, label: str) -> dict | None:
    r = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                         params={"interval": "1d", "range": "5d"}, headers=UA)
    if r.status_code >= 400:
        return None
    result = (((r.json().get("chart") or {}).get("result")) or [None])[0]
    if not result:
        return None
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice") or meta.get("previousClose")
    if price is None:
        return None
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    return {"name": meta.get("shortName") or label, "price": float(price),
            "chg_pct": ((float(price) - float(prev)) / float(prev) * 100) if prev else None,
            "currency": meta.get("currency") or "USD", "as_of": _now(), "extra": "",
            "url": f"https://finance.yahoo.com/quote/{symbol}", "site": "Yahoo Finance"}


# ---------------------------------------------------------------- 源路由

def plan_sources(question: str) -> list[str]:
    """按问题选源。默认只抓汇率这一个宏观锚点，避免无关行情充数。"""
    q = (question or "").lower()
    want: list[str] = []
    if any(k in q for k in ("黄金", "金价", "gold", "白银", "避险", "通胀", "美元")):
        want += ["gold_sina", "silver_sina", "gold_em"]
    if any(k in q for k in ("比特", "btc", "加密", "以太", "eth", "虚拟货币")):
        want += ["btc_binance", "btc_gecko"]
    if any(k in q for k in ("原油", "油价", "能源", "石油")):
        want += ["oil_sina"]
    if any(k in q for k in ("股", "a股", "大盘", "指数", "上证", "牛市", "熊市", "泡沫", "估值")):
        want += ["sh_index", "cyb_index"]
    if any(k in q for k in ("房价", "楼市", "房贷", "利率", "汇率", "经济", "宏观")):
        want += ["usdcny", "sh_index"]
    if not want:
        want = ["usdcny"]
    seen, out = set(), []
    for w in want:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


_LABELS = {
    "gold_sina": "纽约黄金（新浪）", "silver_sina": "纽约白银（新浪）",
    "gold_em": "COMEX 黄金（东财）", "oil_sina": "美原油（新浪）",
    "usdcny": "美元兑人民币", "btc_binance": "BTC/USDT（Binance）",
    "btc_gecko": "Bitcoin（CoinGecko）", "sh_index": "上证指数",
    "cyb_index": "创业板指",
}


async def _run_one(client: httpx.AsyncClient, key: str) -> dict | None:
    if key == "gold_sina":
        return await _sina_hf(client, "hf_GC", "纽约黄金")
    if key == "silver_sina":
        return await _sina_hf(client, "hf_SI", "纽约白银")
    if key == "oil_sina":
        return await _sina_hf(client, "hf_CL", "美原油")
    if key == "gold_em":
        return await _eastmoney_gold(client)
    if key == "usdcny":
        return await _usd_cny(client)
    if key == "btc_binance":
        return await _binance(client, "BTCUSDT", "Bitcoin")
    if key == "btc_gecko":
        return await _coingecko(client)
    if key == "sh_index":
        return await _em_index(client, "1.000001", "上证指数")
    if key == "cyb_index":
        return await _em_index(client, "0.399006", "创业板指")
    return None


# ---------------------------------------------------------------- 对外

async def collect_market(question: str,
                         emit: Callable[[str, dict], Awaitable[None]] | None = None
                         ) -> tuple[list[Evidence], list[Gap]]:
    keys = plan_sources(question)
    evidences: list[Evidence] = []
    gaps: list[Gap] = []

    async def say(ev, data):
        if emit:
            await emit(ev, data)

    await say("fetch_plan", {"sources": [{"key": k, "label": _LABELS.get(k, k)} for k in keys]})

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        results = await asyncio.gather(*[_run_one(client, k) for k in keys],
                                       return_exceptions=True)
        for key, res in zip(keys, results):
            label = _LABELS.get(key, key)
            if isinstance(res, Exception) or not res or res.get("price") is None:
                gaps.append(Gap(kind="retrieval_failed", topic=label,
                                queries_tried=[key], scope="公开行情接口",
                                note="单源失败已跳过，不影响其他来源"))
                await say("signal", {"key": key, "label": label, "status": "failed"})
                continue
            value = f"{_fmt(float(res['price']))} {res.get('currency') or ''}".strip()
            chg = res.get("chg_pct")
            chg_s = f"（{chg:+.2f}%）" if isinstance(chg, (int, float)) else ""
            ev = Evidence(
                url=res.get("url", ""), title=f"{res['name']} 实时报价",
                excerpt=f"{res['name']} 最新价 {value}{chg_s}，抓取时点 {res.get('as_of')}。"
                        f"{res.get('extra') or ''}",
                captured_at=_now(), published_at=datetime.now(CST).strftime("%Y-%m-%d"),
                source_type="finance_media", collected_by="price",
                fetch_status="sourced", value=value,
            )
            credibility.apply(ev)
            evidences.append(ev)
            await say("signal", {"key": key, "label": label, "status": "ok",
                                 "value": value, "chg": chg, "as_of": res.get("as_of"),
                                 "site": res.get("site", ""), "ev_id": ev.ev_id})

        # 黄金全线失败时最后试一次 Yahoo（本机常可用，服务器常不可达）
        if any(k.startswith("gold") for k in keys) and not any("黄金" in e.title for e in evidences):
            try:
                y = await _yahoo(client, "GC=F", "COMEX 黄金")
                if y:
                    ev = Evidence(url=y["url"], title=f"{y['name']} 实时报价",
                                  excerpt=f"{y['name']} 最新价 {_fmt(y['price'])} {y['currency']}，"
                                          f"抓取时点 {y['as_of']}",
                                  captured_at=_now(), source_type="finance_media",
                                  collected_by="price", value=_fmt(y["price"]))
                    credibility.apply(ev)
                    evidences.append(ev)
                    await say("signal", {"key": "gold_yahoo", "label": "COMEX 黄金（Yahoo）",
                                         "status": "ok", "value": _fmt(y["price"]),
                                         "site": "Yahoo Finance", "ev_id": ev.ev_id})
            except Exception:
                pass

    await say("fetch_done", {"ok": len(evidences), "failed": len(gaps)})
    return evidences, gaps


def market_block(evidences: list[Evidence], gaps: list[Gap]) -> str:
    """给模型看的行情快照块。带 ev_id，方便模型在论点里引用。"""
    lines = ["# 实时公开行情快照（服务器刚抓取，时区 CST）",
             f"- 抓取时刻：**{_now()}**",
             "- 这些是此刻可核验的数字。研判的「现在」必须锚定在这个时点。", ""]
    if evidences:
        lines.append("## 已取证信号")
        for e in evidences:
            lines.append(f"- [{e.ev_id}] {e.excerpt}（来源 {e.domain}，可信度 {e.credibility}）")
    else:
        lines.append("## 已取证信号\n- （本次公开行情接口全部未返回）")
    if gaps:
        lines.append("")
        lines.append("## 取证缺口（必须如实写进报告，不得用估算数字填补）")
        for g in gaps:
            lines.append(f"- {g.statement()}")
    return "\n".join(lines)
