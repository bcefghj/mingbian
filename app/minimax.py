# -*- coding: utf-8 -*-
"""MiniMax 引擎（OpenAI 兼容流式）。体验上对齐「思维流 + 专家派遣」：先播报派遣过程，再流式出报告。"""
import os
import json
import asyncio
import httpx
from .prompts import SYSTEM_METHODOLOGY, EXPERT_ROSTER
from .experts import pick_experts
from .infini import split_meta, clean_markdown

BASE = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
KEY = os.getenv("MINIMAX_API_KEY", "")
MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
TIMEOUT = int(os.getenv("ANALYZE_TIMEOUT", "180"))


async def _thought(emit, kind, text, expert=None, step=None):
    payload = {"kind": kind, "text": text}
    if expert:
        payload["expert"] = expert
    if step:
        payload["step"] = step
    await emit("thought", payload)


async def _prologue(question: str, emit):
    """开场：拆解 → 派遣专家 → 取证声明（让用户立刻感到「活着」）。"""
    experts = pick_experts(question)
    await emit("status", {"step": "intake", "message": "拆解问题 · 组建专家团"})
    await _thought(emit, "plan", f"核心问题：「{question}」。先拆变量、定时间窗口，再按相关度派遣专家。", step="intake")
    await asyncio.sleep(0.25)
    names = "、".join(e["name"] for e in experts)
    await _thought(emit, "dispatch", f"本次派遣 {len(experts)} 位：{names}", step="intake")
    await emit("experts", {"keys": [e["key"] for e in experts]})
    for e in experts:
        await asyncio.sleep(0.18)
        await _thought(
            emit, "dispatch",
            f"派遣【{e['name']}】——{e['role'][:42]}…",
            expert=e["key"], step="intake",
        )
        await emit("expert_on", {"key": e["key"]})
    await emit("status", {"step": "collect", "message": "多源取证 · 抽取信号与实体"})
    await _thought(emit, "action", "专家团并行取证中：市场数据 / 宏观指标 / 舆情与公开记录交叉核对…", step="collect")
    await asyncio.sleep(0.15)
    await _thought(emit, "action", "关联溯源：抽取实体，寻找隐藏关系与一致性信号…", expert="entity", step="collect")
    return experts


async def run_analysis(question: str, task_text: str, emit):
    if not KEY:
        raise RuntimeError("MINIMAX_API_KEY 未配置")
    await emit("status", {"step": "plan", "message": "专家团就绪，开始取证"})
    await _prologue(question, emit)

    roster = "\n".join(f"- {e['name']}：{e['role']}" for e in EXPERT_ROSTER)
    sys = SYSTEM_METHODOLOGY.replace("{roster}", roster)
    body = {
        "model": MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": sys},
            {
                "role": "user",
                "content": (
                    "请研判：" + question.strip()
                    + "\n产出 Markdown 报告 + sinan-meta JSON 两部分，"
                    "覆盖至少3个维度并含红队反方。证据尽量给具体数字/出处。"
                ),
            },
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
                raise RuntimeError(f"MiniMax HTTP {r.status_code}: {err!r}")
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
                    # 节流，避免前端狂刷
                    now = asyncio.get_event_loop().time()
                    if now - last_emit >= 0.35 or len(md) < 80:
                        last_emit = now
                        disp, _ = split_meta(md)
                        # 流式阶段也尽量藏住思维链
                        if "<think>" in md.lower() and "</think>" not in md.lower():
                            continue  # 思维块未闭合前不推给前端
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
            {"role": "system", "content": "你是司南的深化研判官。坚持无证据不立论，只输出增量 Markdown。"},
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
            raise RuntimeError(f"MiniMax HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        return (((j.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
