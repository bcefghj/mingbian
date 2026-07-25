# -*- coding: utf-8 -*-
"""实时公开行情抓取（对齐数字先知「先取证再研判」）。

阿里云中国区访问不了 Yahoo/Binance/CoinGecko，因此优先：
新浪财经期货行情、东财 push2、open.er-api 汇率。
本机开发环境仍可回退 Yahoo。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Awaitable

import httpx

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
}
CST = timezone(timedelta(hours=8))


def _now_cst() -> datetime:
    return datetime.now(CST)


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
    chg_s = f"，较昨结/昨收 {chg:+.2f}%" if isinstance(chg, (int, float)) else ""
    extra = f"，{item['extra']}" if item.get("extra") else ""
    unit = item.get("currency") or ""
    return f"- {label}：{_fmt_price(float(p))} {unit}{chg_s}{extra}｜抓取时点 {item.get('as_of')}"


async def _sina_hf(client: httpx.AsyncClient, code: str, label: str) -> dict[str, Any] | None:
    """新浪国际期货：hf_GC / hf_SI / hf_CL ..."""
    url = f"https://hq.sinajs.cn/list={code}"
    r = await client.get(url, headers={**UA, "Referer": "https://finance.sina.com.cn"})
    if r.status_code >= 400:
        return None
    # var hq_str_hf_GC="4058.945,,4055.200,...";
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
    day = parts[12] if len(parts) > 12 else ""
    tim = parts[6] if len(parts) > 6 else ""
    name = parts[13] if len(parts) > 13 else label
    extra_bits = []
    if high is not None and low is not None:
        extra_bits.append(f"日内 {low:.2f}-{high:.2f}")
    return {
        "symbol": code,
        "name": name,
        "price": price,
        "prev": prev,
        "chg_pct": chg,
        "currency": "USD",
        "as_of": f"{day} {tim} CST".strip(),
        "extra": "，".join(extra_bits),
        "source": "sina",
    }


async def _eastmoney_gold(client: httpx.AsyncClient) -> dict[str, Any] | None:
    """东财 COMEX 黄金 GC00Y。f43 常为价格*10。"""
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    r = await client.get(
        url,
        params={"secid": "101.GC00Y", "fields": "f43,f57,f58,f60,f169,f170"},
        headers=UA,
    )
    if r.status_code >= 400:
        return None
    d = (r.json() or {}).get("data") or {}
    if not d:
        return None
    raw = d.get("f43")
    prev_raw = d.get("f60")
    if raw is None:
        return None
    # 经验：40557 -> 4055.7
    price = float(raw) / 10.0 if float(raw) > 10000 else float(raw)
    prev = None
    if prev_raw is not None:
        prev = float(prev_raw) / 10.0 if float(prev_raw) > 10000 else float(prev_raw)
    chg = ((price - prev) / prev * 100) if prev else None
    return {
        "symbol": "GC00Y",
        "name": d.get("f58") or "COMEX黄金",
        "price": price,
        "prev": prev,
        "chg_pct": chg,
        "currency": "USD",
        "as_of": _now_cst().strftime("%Y-%m-%d %H:%M CST"),
        "extra": "",
        "source": "eastmoney",
    }


async def _usd_cny(client: httpx.AsyncClient) -> dict[str, Any] | None:
    r = await client.get("https://open.er-api.com/v6/latest/USD", headers=UA)
    if r.status_code >= 400:
        return None
    j = r.json()
    if j.get("result") != "success":
        return None
    rates = j.get("rates") or {}
    cny = rates.get("CNY")
    if cny is None:
        return None
    return {
        "symbol": "USD/CNY",
        "name": "美元兑人民币",
        "price": float(cny),
        "prev": None,
        "chg_pct": None,
        "currency": "",
        "as_of": (j.get("time_last_update_utc") or _now_cst().strftime("%Y-%m-%d")) + " (er-api)",
        "extra": "",
        "source": "er-api",
    }


async def _yahoo_last(client: httpx.AsyncClient, symbol: str) -> dict[str, Any] | None:
    """本机/海外备用。"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = await client.get(url, params={"interval": "1d", "range": "5d"}, headers=UA)
    if r.status_code >= 400:
        return None
    result = (((r.json().get("chart") or {}).get("result")) or [None])[0]
    if not result:
        return None
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice") or meta.get("previousClose")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None:
        return None
    chg = None
    if prev:
        try:
            chg = (float(price) - float(prev)) / float(prev) * 100
        except Exception:
            pass
    ts = meta.get("regularMarketTime")
    as_of = (
        datetime.fromtimestamp(float(ts), tz=CST).strftime("%Y-%m-%d %H:%M CST")
        if ts else _now_cst().strftime("%Y-%m-%d %H:%M CST")
    )
    return {
        "symbol": symbol,
        "name": meta.get("shortName") or symbol,
        "price": float(price),
        "prev": float(prev) if prev else None,
        "chg_pct": chg,
        "currency": meta.get("currency") or "USD",
        "as_of": as_of,
        "extra": "",
        "source": "yahoo",
    }


async def collect_live_signals(
    question: str = "",
    emit: Callable[[str, dict], Awaitable[None]] | None = None,
) -> str:
    now = _now_cst().strftime("%Y-%m-%d %H:%M CST")
    q = (question or "").lower()
    want_crypto = any(k in q for k in ("比特", "btc", "加密", "以太", "eth"))
    want_gold = any(k in q for k in ("黄金", "金价", "gold", "白银", "避险")) or True

    rows: list[str] = [
        "# 实时公开行情快照（服务器刚抓取，时区 CST）",
        f"- 抓取时刻：**{now}**",
        "- 用途：以下数字是「此刻」可核验的公开行情；研判必须以此为当前锚点，禁止写成 2024/2025 的旧语境。",
        "",
        "## 已抓取信号",
    ]
    ok = 0
    seen_labels: set[str] = set()

    async def add(item: dict[str, Any] | None, label: str):
        nonlocal ok
        if not item or item.get("price") is None:
            if emit:
                await emit("thought", {"kind": "action", "text": f"取证失败（跳过）：{label}", "step": "collect"})
            return
        if label in seen_labels:
            return
        seen_labels.add(label)
        ok += 1
        rows.append(_line(item, label))
        if emit:
            await emit("thought", {
                "kind": "action",
                "text": f"已取证：{label} = {_fmt_price(float(item['price']))}（{item.get('as_of')}）",
                "step": "collect",
            })

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        # 1) 中国区优先：新浪 + 东财
        jobs = []
        if want_gold:
            jobs.append(("新浪 纽约黄金 hf_GC", _sina_hf(client, "hf_GC", "纽约黄金")))
            jobs.append(("新浪 纽约白银 hf_SI", _sina_hf(client, "hf_SI", "纽约白银")))
            jobs.append(("东财 COMEX黄金", _eastmoney_gold(client)))
        jobs.append(("新浪 美原油 hf_CL", _sina_hf(client, "hf_CL", "美原油")))
        jobs.append(("USD/CNY 汇率", _usd_cny(client)))
        if want_crypto:
            # 币安在阿里云常不可达，仍尝试；失败即跳过
            async def _binance_btc():
                try:
                    r = await client.get(
                        "https://api.binance.com/api/v3/ticker/price",
                        params={"symbol": "BTCUSDT"},
                        headers=UA,
                    )
                    if r.status_code >= 400:
                        return None
                    p = float(r.json()["price"])
                    return {
                        "symbol": "BTCUSDT", "name": "Bitcoin", "price": p,
                        "prev": None, "chg_pct": None, "currency": "USDT",
                        "as_of": now, "extra": "", "source": "binance",
                    }
                except Exception:
                    return None
            jobs.append(("Binance BTCUSDT", _binance_btc()))

        # 并行
        labels = [j[0] for j in jobs]
        results = await asyncio.gather(*[j[1] for j in jobs], return_exceptions=True)
        for lab, res in zip(labels, results):
            if isinstance(res, Exception):
                await add(None, lab)
            else:
                await add(res, lab)

        # 2) 若黄金仍空，再试 Yahoo（本机常可用）
        if not any("黄金" in x for x in seen_labels):
            try:
                y = await _yahoo_last(client, "GC=F")
                await add(y, "Yahoo COMEX黄金 GC=F")
            except Exception:
                await add(None, "Yahoo COMEX黄金 GC=F")

    rows.append("")
    rows.append("## 抓取统计")
    rows.append(f"- 成功信号数：{ok}")
    rows.append("- 来源优先：新浪财经 / 东财 / open.er-api（适配中国区服务器）；Yahoo/Binance 为可选回退")
    rows.append("- 约束：报告标题日期、结论「现在」必须对齐上方抓取时刻；引用历史数据时写清「数据截至」。")
    if ok == 0:
        rows.append("- ⚠ 本次公开接口均未拉到数，请诚实说明「实时行情抓取失败」，不要编造点位。")
    return "\n".join(rows)
