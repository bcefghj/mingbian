# -*- coding: utf-8 -*-
"""MiniMax 降级通道（OpenAI 兼容流式）。

只在 InfiniSynapse 不可用时接管，并且界面上会明确标注「已降级」——
不声不响地换引擎，对比赛核验和用户信任都是不诚实的。
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx

from .infini import clean_markdown, split_meta

BASE = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
KEY = os.getenv("MINIMAX_API_KEY", "")
MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
TIMEOUT = int(os.getenv("ANALYZE_TIMEOUT", "300"))


async def run_task(prompt: str, emit=None, *, purpose: str = "analyze",
                   timeout: int | None = None) -> dict:
    if not KEY:
        raise RuntimeError("MINIMAX_API_KEY 未配置")
    timeout = timeout or TIMEOUT
    started = asyncio.get_event_loop().time()

    body = {"model": MODEL, "stream": True,
            "messages": [{"role": "user", "content": prompt}]}

    md, last_emit = "", 0.0
    async with httpx.AsyncClient(timeout=timeout + 30) as client:
        async with client.stream("POST", f"{BASE}/chat/completions",
                                 headers={"Authorization": f"Bearer {KEY}",
                                          "Content-Type": "application/json"},
                                 json=body) as r:
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
                except Exception:
                    continue
                delta = (((j.get("choices") or [{}])[0]).get("delta") or {}).get("content") or ""
                if not delta:
                    continue
                md += delta
                now = asyncio.get_event_loop().time()
                if emit and (now - last_emit >= 0.35 or len(md) < 80):
                    last_emit = now
                    if "<think>" in md.lower() and "</think>" not in md.lower():
                        continue
                    disp, _ = split_meta(md)
                    if disp:
                        await emit("text", {"markdown": disp})

    if not md.strip():
        raise RuntimeError("MiniMax 未返回内容")
    display, meta = split_meta(md)
    return {"taskId": None, "markdown": display, "meta": meta, "share_url": "",
            "engine": "minimax", "model": MODEL,
            "elapsed_ms": int((asyncio.get_event_loop().time() - started) * 1000)}
