# -*- coding: utf-8 -*-
"""实时公开行情抓取（对齐数字先知「先取证再研判」）。

不依赖 Infini 联网：直接从 Yahoo Chart / Stooq / CoinGecko 等公开接口拉数，
注入 prompt，逼模型用「此刻」的数字，而不是训练记忆里的 2024/2025。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Awaitable

import httpx

UA = {
    "User-Agent": "Mozilla/5.0 (compatible; SINAN/1.0; +https://github.com/bcefghj/sinan)",
    "Accept": "application/json,text/plain,*/*",
}
CST = timezone(timedelta(hours=8))


def _now_cst() -> datetime:
    return datetime.now(CST)


def _fmt_ts(ts: int | float | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=CST).strftime("%Y-%m-%d %H:%M CST")
    except Exception:
        return ""


async def _yahoo_last(client: httpx.AsyncClient, symbol: str) -> dict[str, Any] | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = await client.get(url, params={"interval": "1d", "range": "10d"}, headers=UA)
    if r.status_code >= 400:
        return None
    j = r.json()
    result = ((j.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return None
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice") or meta.get("previousClose")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    ts = meta.get("regularMarketTime")
    currency = meta.get("currency") or ""
    name = meta.get("shortName") or meta.get("symbol") or symbol
    chg = None
    if price is not None and prev:
        try:
            chg = (float(price) - float(prev)) / float(prev) * 100
        except Exception:
            chg = None
    # 近段收盘，用于简单趋势
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    closes = [c for c in closes if c is not None]
    trend = ""
    if len(closes) >= 5:
        a, b = closes[-5], closes[-1]
        if a and b:
            trend = f"近5日 {(b - a) / a * 100:+.2f}%"
    return {
        "symbol": symbol,
        "name": name,
        "price": float(price) if price is not None else None,
        "prev": float(prev) if prev is not None else None,
        "chg_pct": chg,
        "currency": currency,
        "as_of": _fmt_ts(ts) or _now_cst().strftime("%Y-%m-%d %H:%M CST"),
        "trend": trend,
    }


async def _stooq_last(client: httpx.AsyncClient, symbol: str) -> dict[str, Any] | None:
    """备用：stooq 日线 CSV 最后一行。"""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    r = await client.get(url, headers=UA)
    if r.status_code >= 400 or not r.text or "Date" not in r.text[:40]:
        return None
    lines = [ln.strip() for ln in r.text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    # Date,Open,High,Low,Close,Volume
    parts = lines[-1].split(",")
    if len(parts) < 5:
        return None
    try:
        close = float(parts[4])
    except Exception:
        return None
    return {
        "symbol": symbol,
        "name": symbol.upper(),
        "price": close,
        "prev": None,
        "chg_pct": None,
        "currency": "USD",
        "as_of": parts[0] + " (stooq daily)",
        "trend": "",
    }


async def _coingecko_btc(client: httpx.AsyncClient) -> dict[str, Any] | None:
    url = "https://api.coingecko.com/api/v3/simple/price"
    r = await client.get(
        url,
        params={"ids": "bitcoin", "vs_currencies": "usd", "include_24hr_change": "true"},
        headers=UA,
    )
    if r.status_code >= 400:
        return None
    j = r.json().get("bitcoin") or {}
    if "usd" not in j:
        return None
    return {
        "symbol": "BTC-USD",
        "name": "Bitcoin",
        "price": float(j["usd"]),
        "prev": None,
        "chg_pct": float(j["usd_24h_change"]) if j.get("usd_24h_change") is not None else None,
        "currency": "USD",
        "as_of": _now_cst().strftime("%Y-%m-%d %H:%M CST"),
        "trend": "24h",
    }


async def _fear_greed(client: httpx.AsyncClient) -> dict[str, Any] | None:
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    r = await client.get(
        url,
        headers={
            **UA,
            "Referer": "https://edition.cnn.com/",
        },
    )
    if r.status_code >= 400:
        return None
    j = r.json()
    fg = j.get("fear_and_greed") or {}
    score = fg.get("score")
    if score is None:
        return None
    return {
        "symbol": "CNN-FG",
        "name": "CNN Fear & Greed",
        "price": float(score),
        "prev": None,
        "chg_pct": None,
        "currency": "",
        "as_of": _now_cst().strftime("%Y-%m-%d %H:%M CST"),
        "trend": str(fg.get("rating") or ""),
    }


def _fmt_price(p: float) -> str:
    if abs(p) >= 1000:
        return f"{p:,.2f}"
    if abs(p) >= 10:
        return f"{p:.2f}"
    return f"{p:.4f}"


def _line(item: dict[str, Any], label: str) -> str:
    p = item.get("price")
    if p is None:
        return f"- {label}：暂无"
    chg = item.get("chg_pct")
    chg_s = f"，当日/近变 {chg:+.2f}%" if isinstance(chg, (int, float)) else ""
    trend = f"，{item['trend']}" if item.get("trend") else ""
    unit = item.get("currency") or ""
    return f"- {label}：{_fmt_price(float(p))} {unit}{chg_s}{trend}｜抓取时点 {item.get('as_of')}"


async def collect_live_signals(
    question: str = "",
    emit: Callable[[str, dict], Awaitable[None]] | None = None,
) -> str:
    """并行抓取一批公开行情，返回可注入 prompt 的 Markdown 文本块。"""
    now = _now_cst().strftime("%Y-%m-%d %H:%M CST")
    q = (question or "").lower()
    want_crypto = any(k in q for k in ("比特", "btc", "加密", "以太", "eth"))
    want_gold = any(k in q for k in ("黄金", "金价", "gold", "白银", "避险")) or not want_crypto
    want_macro = True

    rows: list[str] = [
        f"# 实时公开行情快照（服务器刚抓取，时区 CST）",
        f"- 抓取时刻：**{now}**",
        f"- 用途：以下数字是「此刻」可核验的公开行情；研判必须以此为当前锚点，禁止写成 2024/2025 的旧语境。",
        "",
        "## 已抓取信号",
    ]
    ok = 0

    async with httpx.AsyncClient(timeout=18.0, follow_redirects=True) as client:
        tasks = []
        # 黄金 / 白银 / 美元 / 美债 / 标普
        symbols = []
        if want_gold or want_macro:
            symbols += [("GC=F", "COMEX 黄金期货 GC=F"), ("SI=F", "COMEX 白银期货 SI=F")]
        if want_macro:
            symbols += [
                ("DX-Y.NYB", "美元指数 DXY"),
                ("^TNX", "美国10年期国债收益率 ^TNX"),
                ("^VIX", "VIX 恐慌指数"),
                ("SPY", "标普500 ETF SPY"),
            ]
        if want_crypto or want_macro:
            symbols += [("BTC-USD", "比特币 BTC-USD")]

        async def one_yahoo(sym: str, label: str):
            nonlocal ok
            try:
                item = await _yahoo_last(client, sym)
                if not item and "gc" in sym.lower():
                    item = await _stooq_last(client, "xauusd")
                    if item:
                        label = "黄金现货 XAUUSD (stooq)"
                if not item and "DX" in sym:
                    item = await _yahoo_last(client, "DX=F")
            except Exception:
                item = None
            if item and item.get("price") is not None:
                ok += 1
                line = _line(item, label)
                rows.append(line)
                if emit:
                    await emit("thought", {
                        "kind": "action",
                        "text": f"已取证：{label} = {_fmt_price(float(item['price']))}（{item.get('as_of')}）",
                        "step": "collect",
                    })
                return item
            if emit:
                await emit("thought", {
                    "kind": "action",
                    "text": f"取证失败（跳过）：{label}",
                    "step": "collect",
                })
            return None

        for sym, label in symbols:
            tasks.append(one_yahoo(sym, label))

        async def extras():
            nonlocal ok
            try:
                if want_crypto or want_macro:
                    btc = await _coingecko_btc(client)
                    if btc and btc.get("price") is not None:
                        ok += 1
                        rows.append(_line(btc, "比特币 CoinGecko 现价"))
                        if emit:
                            await emit("thought", {
                                "kind": "action",
                                "text": f"已取证：CoinGecko BTC = {_fmt_price(float(btc['price']))}",
                                "step": "collect",
                            })
            except Exception:
                pass
            try:
                fg = await _fear_greed(client)
                if fg and fg.get("price") is not None:
                    ok += 1
                    rows.append(
                        f"- CNN Fear&Greed：{fg['price']:.1f}（{fg.get('trend') or ''}）｜抓取时点 {fg.get('as_of')}"
                    )
                    if emit:
                        await emit("thought", {
                            "kind": "action",
                            "text": f"已取证：Fear&Greed = {fg['price']:.1f}",
                            "step": "collect",
                        })
            except Exception:
                pass

        await asyncio.gather(*tasks, return_exceptions=True)
        await extras()

    rows.append("")
    rows.append(f"## 抓取统计")
    rows.append(f"- 成功信号数：{ok}")
    rows.append("- 来源：Yahoo Finance Chart API / Stooq / CoinGecko / CNN Fear&Greed（公开接口）")
    rows.append("- 约束：报告标题日期、结论「现在」必须对齐上方抓取时刻；引用历史数据时写清「数据截至」。")
    if ok == 0:
        rows.append("- ⚠ 本次公开接口均未拉到数，请诚实说明「实时行情抓取失败」，不要编造点位。")
    return "\n".join(rows)
