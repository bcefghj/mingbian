# -*- coding: utf-8 -*-
"""MiniMax 引擎（OpenAI 兼容流式）。先抓公开实时行情，再流式成文——对齐数字先知「先取证再研判」。"""
import os
import json
import asyncio
import httpx
from datetime import date
from .prompts import SYSTEM_METHODOLOGY, EXPERT_ROSTER
from .experts import pick_experts
from .infini import split_meta, clean_markdown
from .live_signals import collect_live_signals

BASE = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
KEY = os.getenv("MINIMAX_API_KEY", "")
MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
TIMEOUT = int(os.getenv("ANALYZE_TIMEOUT", "180"))


def _today_cn() -> str:
    d = date.today()
    return f"{d.year}年{d.month}月{d.day}日"


async def _thought(emit, kind, text, expert=None, step=None):
    payload = {"kind": kind, "text": text}
    if expert:
        payload["expert"] = expert
    if step:
        payload["step"] = step
    await emit("thought", payload)


async def _prologue(question: str, emit):
    experts = pick_experts(question)
    await emit("status", {"step": "intake", "message": "拆解问题 · 组建专家团"})
    await _thought(emit, "plan", f"核心问题：「{question}」。先拆变量、定时间窗口，再按相关度派遣专家。", step="intake")
    await asyncio.sleep(0.2)
    names = "、".join(e["name"] for e in experts)
    await _thought(emit, "dispatch", f"本次派遣 {len(experts)} 位：{names}", step="intake")
    await emit("experts", {"keys": [e["key"] for e in experts]})
    for e in experts:
        await asyncio.sleep(0.12)
        await _thought(
            emit, "dispatch",
            f"派遣【{e['name']}】——{e['role'][:42]}…",
            expert=e["key"], step="intake",
        )
        await emit("expert_on", {"key": e["key"]})
    return experts


async def run_analysis(question: str, task_text: str, emit):
    if not KEY:
        raise RuntimeError("MINIMAX_API_KEY 未配置")
    today = _today_cn()
    await emit("status", {"step": "plan", "message": "专家团就绪，开始取证"})
    await _prologue(question, emit)

    # —— 关键：像数字先知一样先拉真实行情 ——
    await emit("status", {"step": "collect", "message": "多源取证 · 拉取公开实时行情"})
    await _thought(emit, "action", "正在连接公开行情源（Yahoo / Stooq / CoinGecko）…", step="collect")

    async def _emit_bridge(event, data):
        await emit(event, data)

    live_block = await collect_live_signals(question, emit=_emit_bridge)
    await _thought(emit, "finding", "公开行情快照已就绪，转入交叉研判…", step="collect")

    roster = "\n".join(f"- {e['name']}：{e['role']}" for e in EXPERT_ROSTER)
    sys = (
        SYSTEM_METHODOLOGY
        .replace("{roster}", roster)
        .replace("{today}", today)
        + "\n\n# 额外硬约束（实时取证模式）\n"
        "用户消息里附有「实时公开行情快照」。这是服务器刚刚抓到的数字。\n"
        f"- 今天是 {today}；报告标题日期必须是 {today}（或抓取时刻所在日），严禁写成 2024/2025。\n"
        "- 凡涉及「现在金价/汇率/利率/股指」优先引用快照里的数字与抓取时点。\n"
        "- 快照没有的历史序列，可引用公开历史并写明「数据截至」；不得把旧年份伪装成今天。\n"
        "- sinan-meta.as_of 填快照日期（YYYY-MM）。\n"
    )

    user_content = (
        f"请研判：{question.strip()}\n\n"
        f"{live_block}\n\n"
        f"今天是 {today}。请基于上方实时快照 + 你的公开知识交叉研判，"
        "产出 Markdown 报告 + sinan-meta JSON；覆盖至少3个维度并含红队反方。"
        "只输出最终中文报告，不要思考过程。"
    )

    body = {
        "model": MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": user_content},
        ],
    }

    await emit("status", {"step": "analyze", "message": "交叉研判 · 关联发现 · 概率场景"})
    await _thought(emit, "finding", "首席研判官开始综合成文（流式输出）…", step="analyze")

    md = ""
    last_emit = 0.0
    async with httpx.AsyncClient(timeout=TIMEOUT + 30) as client:
        async with client.stream(
            "POST",
            f"{BASE}/chat/completions",
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            json=body,
        ) as r:
            if r.status_code >= 400:
                err = (await r.aread())[:240]
                raise RuntimeError(f"HTTP {r.status_code}: {err!r}")
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    j = json.loads(chunk)
                    delta = (((j.get("choices") or [{}])[0]).get("delta") or {}).get("content") or ""
                    if not delta:
                        continue
                    md += delta
                    now = asyncio.get_event_loop().time()
                    if now - last_emit >= 0.35 or len(md) < 80:
                        last_emit = now
                        if "<think>" in md.lower() and "</think>" not in md.lower():
                            continue
                        disp, _ = split_meta(md)
                        await emit("text", {"markdown": clean_markdown(disp) or disp})
                except Exception:
                    continue

    if not md.strip():
        raise RuntimeError("未返回内容")
    display, meta = split_meta(md)
    await emit("text", {"markdown": display})
    if meta and meta.get("experts"):
        keys = []
        for e in meta["experts"]:
            k = e.get("key")
            if k == "redteam":
                k = "contra"
            if k:
                keys.append(k)
        await emit("experts", {"keys": keys})
    await _thought(emit, "reflect", "红队反方与证据置信度已写入报告；请核对背离与边界声明。", expert="contra", step="analyze")
    return {"taskId": None, "markdown": display, "meta": meta, "share_url": "", "engine": "minimax"}


async def run_deepen(deepen_text: str) -> str:
    if not KEY:
        raise RuntimeError("MINIMAX_API_KEY 未配置")
    body = {
        "model": MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": f"你是司南的深化研判官。今天是{_today_cn()}。坚持无证据不立论，只输出增量 Markdown。"},
            {"role": "user", "content": deepen_text},
        ],
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{BASE}/chat/completions",
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            json=body,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        return clean_markdown((((j.get("choices") or [{}])[0]).get("message") or {}).get("content") or "")
