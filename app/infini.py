# -*- coding: utf-8 -*-
"""InfiniSynapse 官方 Server API 客户端（比赛要求，主用引擎）。
流程：先建 SSE -> 开联网 -> newTask -> 轮询 /api/ai_task/tasks 取报告 -> setShare。
额外：从报告里抽取 ```sinan-meta``` 结构化 JSON 供前端可视化。
"""
import os
import re
import json
import uuid
import asyncio
import httpx

BASE = os.getenv("INFINI_BASE_URL", "https://app.infinisynapse.cn").rstrip("/")
KEY = os.getenv("INFINI_API_KEY", "")
WEBSEARCH = os.getenv("INFINI_ENABLE_WEBSEARCH", "true").lower() == "true"
TIMEOUT = int(os.getenv("ANALYZE_TIMEOUT", "300"))


def _h(stream=False):
    h = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "x-lang": "zh_CN"}
    if stream:
        h["Accept"] = "text/event-stream"
    return h


def split_meta(markdown: str):
    """从报告里抽出 sinan-meta JSON；返回 (纯展示markdown, meta_dict_or_None)。"""
    if not markdown:
        return markdown, None
    m = re.search(r"```sinan-meta\s*(\{.*?\})\s*```", markdown, re.S)
    meta = None
    display = markdown
    if m:
        raw = m.group(1)
        try:
            meta = json.loads(raw)
        except Exception:
            try:
                meta = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            except Exception:
                meta = None
        display = (markdown[:m.start()] + markdown[m.end():]).strip()
    return display, meta


def _extract_markdown(task_json: dict) -> str:
    if not isinstance(task_json, dict):
        return ""
    data = task_json.get("data", task_json)
    messages = data.get("messages") or data.get("uiMessages") or []
    texts = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or m.get("type") or m.get("say") or "").lower()
        content = m.get("text") or m.get("content") or m.get("message") or ""
        if isinstance(content, dict):
            content = content.get("text") or content.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in content)
        content = str(content or "").strip()
        if not content or role in ("user", "human", "ask"):
            continue
        texts.append(content)
    return max(texts, key=len) if texts else ""


async def run_analysis(question: str, task_text: str, emit):
    if not KEY:
        raise RuntimeError("INFINI_API_KEY 未配置")
    conn_id = str(uuid.uuid4())
    partial = {"text": ""}
    ready = {"v": False}

    async with httpx.AsyncClient(timeout=None) as client:
        async def sse_listener():
            try:
                async with client.stream("GET", f"{BASE}/api/ai/events?connId={conn_id}",
                                         headers=_h(stream=True), timeout=TIMEOUT + 30) as r:
                    async for line in r.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "ping":
                            continue
                        try:
                            evt = json.loads(raw)
                        except Exception:
                            continue
                        et = evt.get("event") or evt.get("type") or ""
                        pl = evt.get("data") or evt
                        msg = pl.get("message") if isinstance(pl, dict) else None
                        if et in ("message.partial", "message.update", "message.add"):
                            txt = (msg or {}).get("text") or (msg or {}).get("content") or ""
                            if txt and isinstance(txt, str) and len(txt) > len(partial["text"]):
                                partial["text"] = txt
                                disp, _ = split_meta(txt)
                                await emit("text", {"markdown": disp})
                        elif et == "state.ready":
                            ready["v"] = True
            except Exception:
                await emit("status", {"step": "collect", "message": "（实时通道波动，转轮询）"})

        listener = asyncio.create_task(sse_listener())
        await asyncio.sleep(0.6)

        if WEBSEARCH:
            try:
                await client.post(f"{BASE}/api/ai/message", headers=_h(),
                                  json={"type": "autoApprovalSettings",
                                        "autoApprovalSettings": {"enableWebSearch": True}})
            except Exception:
                pass

        await emit("status", {"step": "intake", "message": "拆解问题 · 组建专家团 · 规划取证路径"})
        res = await client.post(f"{BASE}/api/ai/message", headers=_h(),
                                json={"type": "newTask", "connId": conn_id, "text": task_text,
                                      "chatSettings": {"mode": "act"}})
        res.raise_for_status()
        j = res.json()
        task_id = (j.get("state") or {}).get("taskId") or j.get("taskId") or (j.get("data") or {}).get("taskId")
        if task_id:
            await emit("plan", {"taskId": task_id, "message": "任务已创建，专家团联网取证中..."})
        await emit("status", {"step": "collect", "message": "多源取证 · 抽取信号与实体"})

        markdown = ""
        deadline = asyncio.get_event_loop().time() + TIMEOUT
        poll = 0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(4)
            poll += 1
            if not task_id:
                try:
                    lst = await client.get(f"{BASE}/api/ai_task/list", headers=_h())
                    arr = (lst.json().get("data") or {}).get("list") or lst.json().get("list") or []
                    if arr:
                        task_id = arr[0].get("taskId") or arr[0].get("id")
                except Exception:
                    pass
            if not task_id:
                continue
            try:
                tr = await client.get(f"{BASE}/api/ai_task/tasks", params={"taskId": task_id}, headers=_h())
                tj = tr.json()
            except Exception:
                continue
            is_running = tj.get("data", tj).get("isRunning")
            md = _extract_markdown(tj)
            if md and len(md) > len(markdown):
                markdown = md
                disp, _ = split_meta(markdown)
                await emit("text", {"markdown": disp})
            if ready["v"] or is_running is False:
                await asyncio.sleep(1.5)
                try:
                    tr = await client.get(f"{BASE}/api/ai_task/tasks", params={"taskId": task_id}, headers=_h())
                    md2 = _extract_markdown(tr.json())
                    if len(md2) > len(markdown):
                        markdown = md2
                except Exception:
                    pass
                break
            if poll % 2 == 0:
                await emit("status", {"step": "analyze", "message": "交叉研判 · 关联发现 · 概率场景"})

        if partial["text"] and len(partial["text"]) > len(markdown):
            markdown = partial["text"]
        listener.cancel()
        if not markdown.strip():
            raise RuntimeError("InfiniSynapse 未返回有效报告（超时或结构变化）")

        display, meta = split_meta(markdown)
        share_url = ""
        if task_id:
            try:
                await client.post(f"{BASE}/api/ai_task/setShare", headers=_h(),
                                  json={"taskId": task_id, "isPublic": True})
                share_url = f"{BASE}/api/ai_task/publicTask?taskId={task_id}"
            except Exception:
                pass
        return {"taskId": task_id, "markdown": display, "meta": meta,
                "share_url": share_url, "engine": "infini"}


async def run_deepen(deepen_text: str) -> str:
    """批注深化：新起一个轻任务，返回补充 markdown。"""
    conn_id = str(uuid.uuid4())
    async with httpx.AsyncClient(timeout=None) as client:
        try:
            await client.post(f"{BASE}/api/ai/message", headers=_h(),
                              json={"type": "autoApprovalSettings",
                                    "autoApprovalSettings": {"enableWebSearch": WEBSEARCH}})
        except Exception:
            pass
        res = await client.post(f"{BASE}/api/ai/message", headers=_h(),
                                json={"type": "newTask", "connId": conn_id, "text": deepen_text,
                                      "chatSettings": {"mode": "act"}})
        res.raise_for_status()
        j = res.json()
        task_id = (j.get("state") or {}).get("taskId") or j.get("taskId")
        md = ""
        deadline = asyncio.get_event_loop().time() + min(TIMEOUT, 180)
        while asyncio.get_event_loop().time() < deadline and task_id:
            await asyncio.sleep(4)
            try:
                tr = await client.get(f"{BASE}/api/ai_task/tasks", params={"taskId": task_id}, headers=_h())
                tj = tr.json()
            except Exception:
                continue
            m = _extract_markdown(tj)
            if len(m) > len(md):
                md = m
            if tj.get("data", tj).get("isRunning") is False:
                break
        disp, _ = split_meta(md)
        return disp or "（深化未返回内容，请重试）"
