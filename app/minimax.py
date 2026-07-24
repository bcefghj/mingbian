# -*- coding: utf-8 -*-
"""MiniMax 兜底引擎（OpenAI 兼容）。仅当 InfiniSynapse 卡住/失败时启用。"""
import os
import json
import httpx
from .prompts import SYSTEM_METHODOLOGY, EXPERT_ROSTER
from .infini import split_meta

BASE = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
KEY = os.getenv("MINIMAX_API_KEY", "")
MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
TIMEOUT = int(os.getenv("ANALYZE_TIMEOUT", "300"))


async def run_analysis(question: str, task_text: str, emit):
    if not KEY:
        raise RuntimeError("MINIMAX_API_KEY 未配置")
    await emit("status", {"step": "analyze", "message": "（兜底引擎 MiniMax 接管）多智能体研判中..."})
    roster = "\n".join(f"- {e['name']}：{e['role']}" for e in EXPERT_ROSTER)
    sys = SYSTEM_METHODOLOGY.replace("{roster}", roster)
    body = {"model": MODEL, "stream": True, "messages": [
        {"role": "system", "content": sys},
        {"role": "user", "content": "请研判：" + question.strip() +
         "\n产出 Markdown 报告 + sinan-meta JSON 两部分，覆盖至少3个维度并含红队反方。"}]}
    md = ""
    async with httpx.AsyncClient(timeout=TIMEOUT + 30) as client:
        async with client.stream("POST", f"{BASE}/chat/completions",
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                                 json=body) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"MiniMax HTTP {r.status_code}: {(await r.aread())[:160]!r}")
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    j = json.loads(chunk)
                    delta = j["choices"][0].get("delta", {}).get("content") or ""
                    if delta:
                        md += delta
                        disp, _ = split_meta(md)
                        await emit("text", {"markdown": disp})
                except Exception:
                    continue
    if not md.strip():
        raise RuntimeError("MiniMax 未返回内容")
    display, meta = split_meta(md)
    return {"taskId": None, "markdown": display, "meta": meta, "share_url": "", "engine": "minimax"}
