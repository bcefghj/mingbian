# -*- coding: utf-8 -*-
"""InfiniSynapse 官方 Server API 客户端 —— 明辨的主引擎。

比赛前置准入条件要求「后端通过 Server API 调用 InfiniSynapse，调用日志可在平台后台查验」，
因此这里必须跑出真实 taskId，并把模型显式锁定为 deepseek-v4-pro（不依赖账户默认值）。

调用顺序（官方要求，不能颠倒）：
  1. 先建 SSE 长连接 /api/ai/events?connId=
  2. 开联网 autoApprovalSettings.enableWebSearch
  3. newTask
  4. 拿到 taskId 后立刻 /api/ai/settings 锁模型
  5. SSE 流式 + 轮询 /api/ai_task/tasks 取回报告
  6. setShare 生成公开可核验链接
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid

import httpx

BASE = os.getenv("INFINI_BASE_URL", "https://app.infinisynapse.cn").rstrip("/")
KEY = os.getenv("INFINI_API_KEY", "")
MODEL = os.getenv("INFINI_MODEL", "deepseek-v4-pro")
WEBSEARCH = os.getenv("INFINI_ENABLE_WEBSEARCH", "true").lower() == "true"
TIMEOUT = int(os.getenv("INFINI_TIMEOUT", os.getenv("ANALYZE_TIMEOUT", "300")))

# 只认「账号层面确实跑不下去」的措辞。
# 这里的每个词都必须窄到不可能出现在检索到的网页正文里——
# 曾经放过一个「无效」，结果一条讲庞氏骗局的新闻正文命中它，整轮分析被误判为致命错误。
FATAL_HINTS = ("余额不足", "请充值", "账户欠费", "API key 无效", "密钥无效",
               "unauthorized", "Unauthorized", "invalid api key",
               "insufficient balance", "quota exceeded")
# 这些消息类型装的是模型读到的外部内容，不是平台自己的报错，不参与致命判定
_EXTERNAL_SAY = {"web_search", "web_search_result", "web_fetch", "web_fetch_result",
                 "tool_result", "command_output", "browser_action_result",
                 "mcp_server_response", "text", "reasoning"}


def _h(stream: bool = False) -> dict:
    h = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "x-lang": "zh_CN"}
    if stream:
        h["Accept"] = "text/event-stream"
    return h


# ---------------------------------------------------------------- 文本清洗

def clean_markdown(markdown: str) -> str:
    """去掉思维链与英文草稿泄漏，只保留面向用户的中文报告。"""
    if not markdown:
        return markdown
    text = re.sub(r"<think>[\s\S]*?</think>", "", markdown, flags=re.I)
    text = re.sub(r"</?think>", "", text, flags=re.I)
    for pat in (r"\nThe user is asking", r"\nI need to be careful", r"\nLet me structure",
                r"\nNow I'm organizing", r"\nWriting the comprehensive",
                r"\nI should acknowledge", r"\nFor the actual analysis"):
        m = re.search(pat, text)
        if m:
            text = text[: m.start()]
            break
    m = re.search(r"(^|\n)(#\s*[^\n]*[\u4e00-\u9fff][^\n]*)", text)
    if m:
        text = text[m.start(2):]
    return text.strip()


# 认得出「这是我们要的那个 JSON」的特征字段。模型经常把围栏写成
# ```json 而不是 ```mb-meta，只认围栏名会把整份结构化输出丢掉。
_META_KEYS = ("verdict", "claims", "evidence", "dimensions", "base_rate")


def _loads(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        pass
    try:
        return json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception:
        return None


def split_meta(markdown: str):
    """抽出结构化 JSON，返回 (展示用 markdown, meta)。

    三级兜底：正牌 mb-meta 围栏 → 任意代码围栏里长得像 meta 的 JSON →
    裸 JSON 块。兼容旧的 sinan-meta，避免历史 demo 数据失效。
    """
    if not markdown:
        return markdown, None

    m = re.search(r"```(?:mb-meta|sinan-meta)\s*(\{[\s\S]*?\})\s*```", markdown)
    if m:
        meta = _loads(m.group(1))
        if meta is not None:
            return clean_markdown(markdown[:m.start()] + markdown[m.end():]), meta

    # 退而求其次：扫所有代码围栏，挑字段最像 meta 的那一个（通常也是最全的）
    best = None
    for cand in re.finditer(r"```[a-zA-Z-]*\s*(\{[\s\S]*?\})\s*```", markdown):
        obj = _loads(cand.group(1))
        if not isinstance(obj, dict):
            continue
        hits = sum(1 for k in _META_KEYS if k in obj)
        if hits >= 2 and (best is None or hits > best[0]):
            best = (hits, obj, cand.start(), cand.end())
    if best:
        _, meta, s, e = best
        display = markdown[:s] + markdown[e:]
        # 「**mb-meta**」这类残留的标题行也一并清掉
        display = re.sub(r"\n\**\s*mb-meta\s*\**\s*\n", "\n", display)
        return clean_markdown(display), meta

    return clean_markdown(markdown), None


# InfiniSynapse 跑的是 agent 循环，消息流里混着推理、检索、工具调用。
# 这些都不是给用户看的，取错了就会把工具日志当成报告。
_SKIP_SAY = {
    "api_req_started", "api_req_finished", "api_req_retried", "reasoning",
    "web_search", "web_search_result", "web_fetch", "web_fetch_result",
    "tool_result", "checkpoint_created", "command", "command_output",
    "browser_action", "browser_action_result", "mcp_server_request_started",
    "mcp_server_response", "user_feedback", "user_feedback_diff", "error",
    "diff_error", "deleted_api_reqs", "shell_integration_warning",
}
# agent 常把长报告写进文件而不是直接回复，正文得从工具参数里取
_FILE_TOOLS = {"newFileCreated", "editedExistingFile", "fileEdited", "writeToFile"}


def _extract_markdown(task_json: dict) -> str:
    """从 agent 消息流里取出真正的报告正文。

    优先级：写入文件的完整内容 > completion_result > 普通文本回复。
    第一条 say:text 是我们自己发过去的 prompt 回显，必须跳过。
    """
    if not isinstance(task_json, dict):
        return ""
    data = task_json.get("data", task_json)
    messages = data.get("messages") or data.get("uiMessages") or []

    file_text, completion, plain = "", "", []
    first_text_seen = False

    for m in messages:
        if not isinstance(m, dict):
            continue
        if (m.get("type") or "") == "ask":
            continue
        say = (m.get("say") or "").strip()
        content = m.get("text") or m.get("content") or ""
        if isinstance(content, dict):
            content = content.get("text") or content.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(str(x.get("text", x)) if isinstance(x, dict) else str(x)
                                for x in content)
        content = str(content or "").strip()
        if not content:
            continue

        if say == "tool":
            try:
                obj = json.loads(content)
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get("tool") in _FILE_TOOLS:
                body = obj.get("content") or obj.get("diff") or ""
                if isinstance(body, str) and len(body) > len(file_text):
                    file_text = body
            continue

        if say in _SKIP_SAY:
            continue
        if say == "completion_result":
            if len(content) > len(completion):
                completion = content
            continue
        if say in ("text", ""):
            if not first_text_seen:
                first_text_seen = True   # prompt 回显
                continue
            plain.append(content)

    # 文件正文通常是完整报告；completion_result 常只是一段交付说明
    for cand in (file_text, completion):
        if cand and ("mb-meta" in cand or len(cand) > 800):
            return cand
    if plain:
        return max(plain, key=len)
    return file_text or completion or ""


# ---------------------------------------------------------------- 模型锁定

async def lock_model(client: httpx.AsyncClient, task_id: str | None = None) -> str:
    """显式把任务模型设为 deepseek-v4-pro，不依赖账户默认配置。"""
    body: dict = {"apiConfiguration": {"apiProvider": "infinisynapse",
                                       "infinisynapseModelId": MODEL}}
    if task_id:
        body["taskId"] = task_id
    try:
        r = await client.post(f"{BASE}/api/ai/settings", headers=_h(), json=body, timeout=20)
        if r.status_code < 400:
            return MODEL
    except Exception:
        pass
    return MODEL


async def probe() -> dict:
    """健康探针：确认 key 可用、模型是什么。供 /api/health 与台账页使用。"""
    if not KEY:
        return {"ok": False, "reason": "INFINI_API_KEY 未配置", "model": MODEL}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            ping = await client.get(f"{BASE}/api/ai/ping", headers=_h())
            cfg = await client.get(f"{BASE}/api/ai/configuration", headers=_h())
            cfg_model = ""
            try:
                cfg_model = (((cfg.json().get("data") or {}).get("apiConfiguration") or {})
                             .get("infinisynapseModelId") or "")
            except Exception:
                pass
            return {
                "ok": ping.status_code < 400,
                "model": cfg_model or MODEL,
                "configured_model": cfg_model,
                "base": BASE,
            }
    except Exception as e:
        return {"ok": False, "reason": str(e)[:120], "model": MODEL}


# ---------------------------------------------------------------- 主流程

async def run_task(prompt: str, emit=None, *, purpose: str = "analyze",
                   timeout: int | None = None) -> dict:
    """跑一次 Infini 任务，返回 {taskId, markdown, meta, share_url, model, elapsed_ms}。"""
    if not KEY:
        raise RuntimeError("INFINI_API_KEY 未配置")

    timeout = timeout or TIMEOUT
    conn_id = str(uuid.uuid4())
    partial = {"text": "", "tid": None}
    probe: dict = {"id": 0, "last": ""}
    # 同一个账号并发跑多个任务时，事件流里会混进别人的消息。
    # 曾经因此把 A 问题的报告写进了 B 问题的结果里——必须按 taskId 认领。
    own: dict = {"id": None}

    def mine(tid: str) -> bool:
        if not own["id"] or not tid:
            return True
        # 子智能体的 taskId 形如 <父id>_delegate_xxx，也算自己的
        return tid == own["id"] or tid.startswith(f"{own['id']}_")
    ready = {"v": False}
    task_box: dict = {"id": None}
    fatal: dict = {"err": None}
    started = asyncio.get_event_loop().time()

    async def say(event, data):
        if emit:
            await emit(event, data)

    async with httpx.AsyncClient(timeout=None) as client:

        async def sse_listener():
            try:
                async with client.stream("GET", f"{BASE}/api/ai/events?connId={conn_id}",
                                         headers=_h(stream=True), timeout=timeout + 30) as r:
                    async for line in r.aiter_lines():
                        if line.startswith("event:") or not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "ping":
                            continue
                        try:
                            evt = json.loads(raw)
                        except Exception:
                            continue
                        et = evt.get("event") or evt.get("type") or ""
                        pl = evt.get("data") if isinstance(evt.get("data"), dict) else evt
                        if not isinstance(pl, dict):
                            pl = {}
                        tid = pl.get("taskId") or evt.get("taskId")
                        if not mine(tid):
                            continue
                        if tid and not task_box["id"]:
                            task_box["id"] = tid
                            await say("task", {"taskId": tid, "model": MODEL})
                        msg = pl.get("message") if isinstance(pl.get("message"), dict) else {}
                        ask = (msg or {}).get("ask") or ""
                        sy = (msg or {}).get("say") or ""
                        txt = (msg or {}).get("text") or (msg or {}).get("content") or ""
                        if not isinstance(txt, str):
                            txt = ""
                        low = txt[:400].lower()
                        hinted = (sy not in _EXTERNAL_SAY and
                                  any(k.lower() in low for k in FATAL_HINTS))
                        if ask == "api_req_failed" or hinted:
                            fatal["err"] = (txt or "InfiniSynapse API 请求失败")[:160]
                            ready["v"] = True
                            return

                        # 把引擎自己的联网检索透出来，思维流里能看到它在查什么。
                        # 这些字段是逐字流式增长的（"Search" → "Search for" → …），
                        # 一个 token 发一条会刷屏，所以同一句用同一个 id 原地更新。
                        if sy in ("web_search", "web_fetch") and txt:
                            try:
                                o = json.loads(txt)
                                brief = (o.get("brief") or o.get("query")
                                         or o.get("url") or "").strip()
                            except Exception:
                                brief = ""
                            if brief and brief != probe["last"]:
                                if not brief.startswith(probe["last"]) or not probe["last"]:
                                    probe["id"] += 1
                                probe["last"] = brief
                                await say("engine_probe", {"kind": sy, "id": probe["id"],
                                                           "text": brief[:140]})
                            continue
                        if sy in _SKIP_SAY or sy == "tool":
                            continue
                        # 第一条 say:text 是平台把我们的 prompt 回显了一遍
                        if sy == "text" and txt[:48] == prompt[:48]:
                            continue
                        if sy in ("text", "completion_result") and len(txt) > len(partial["text"]):
                            partial["text"] = txt
                            partial["tid"] = tid
                            disp, _ = split_meta(txt)
                            if disp:
                                await say("text", {"markdown": disp})
                        if et == "state.ready":
                            ready["v"] = True
            except Exception:
                await say("status", {"step": "collect", "message": "实时通道波动，转轮询"})

        listener = asyncio.create_task(sse_listener())
        await asyncio.sleep(0.6)

        # 先锁全局模型（任务级的等拿到 taskId 再锁一次）
        await lock_model(client)

        if WEBSEARCH:
            try:
                await client.post(f"{BASE}/api/ai/message", headers=_h(),
                                  json={"type": "autoApprovalSettings",
                                        "autoApprovalSettings": {"enableWebSearch": True}},
                                  timeout=20)
            except Exception:
                pass

        res = await client.post(f"{BASE}/api/ai/message", headers=_h(),
                                json={"type": "newTask", "connId": conn_id, "text": prompt,
                                      "chatSettings": {"mode": "act"}}, timeout=60)
        res.raise_for_status()
        j = res.json()
        task_id = ((j.get("state") or {}).get("taskId") or j.get("taskId")
                   or (j.get("data") or {}).get("taskId")
                   or ((j.get("data") or {}).get("state") or {}).get("taskId")
                   or task_box["id"])

        # POST 的返回值是权威的：它明确告诉我们这次开的是哪个任务。
        # 从事件流里猜到的 taskId 只在返回值缺失时才用。
        if task_id:
            own["id"] = task_id
            if partial["tid"] and not mine(partial["tid"]):
                partial["text"], partial["tid"] = "", None   # 混进来的别人的正文，丢掉
            task_box["id"] = task_id
        else:
            for _ in range(24):
                if task_box["id"] or fatal["err"]:
                    break
                await asyncio.sleep(0.25)
            task_id = task_box["id"] or task_id
            own["id"] = task_id

        if task_id:
            await lock_model(client, task_id)
            await say("task", {"taskId": task_id, "model": MODEL})

        markdown = ""
        deadline = asyncio.get_event_loop().time() + timeout
        poll = 0
        while asyncio.get_event_loop().time() < deadline:
            if fatal["err"]:
                listener.cancel()
                raise RuntimeError(fatal["err"])
            await asyncio.sleep(2)
            poll += 1
            if not task_id:
                task_id = task_box["id"]
            if not task_id:
                try:
                    lst = await client.get(f"{BASE}/api/ai_task/list", headers=_h(), timeout=20)
                    data = lst.json().get("data") or {}
                    arr = data.get("items") or data.get("list") or []
                    if arr:
                        task_id = arr[0].get("taskId") or arr[0].get("id")
                        if task_id:
                            await lock_model(client, task_id)
                            await say("task", {"taskId": task_id, "model": MODEL})
                except Exception:
                    pass
            if not task_id:
                continue
            try:
                tr = await client.get(f"{BASE}/api/ai_task/tasks",
                                      params={"taskId": task_id}, headers=_h(), timeout=30)
                tj = tr.json()
            except Exception:
                continue
            is_running = tj.get("data", tj).get("isRunning")
            md = _extract_markdown(tj)
            if md and len(md) > len(markdown):
                # 真的余额不足时返回的是一句短提示；长文里出现「充值」多半是
                # 报告在讲充值类骗局，不能当成计费错误
                if len(md) < 200 and any(k in md for k in ("余额不足", "请充值")):
                    listener.cancel()
                    raise RuntimeError(md[:120])
                markdown = md
                disp, _ = split_meta(markdown)
                if disp:
                    await say("text", {"markdown": disp})
            if ready["v"] or is_running is False:
                await asyncio.sleep(1.0)
                try:
                    tr = await client.get(f"{BASE}/api/ai_task/tasks",
                                          params={"taskId": task_id}, headers=_h(), timeout=30)
                    md2 = _extract_markdown(tr.json())
                    if len(md2) > len(markdown):
                        markdown = md2
                except Exception:
                    pass
                break
            if poll % 3 == 0:
                await say("status", {"step": purpose, "message": "引擎推理中 · deepseek-v4-pro"})

        if partial["text"] and len(partial["text"]) > len(markdown):
            markdown = partial["text"]
        listener.cancel()
        if fatal["err"]:
            raise RuntimeError(fatal["err"])
        if not markdown.strip():
            raise RuntimeError("InfiniSynapse 未返回有效内容（超时或结构变化）")

        display, meta = split_meta(markdown)
        share_url = ""
        if task_id:
            try:
                await client.post(f"{BASE}/api/ai_task/setShare", headers=_h(),
                                  json={"taskId": task_id, "isPublic": True}, timeout=20)
                share_url = f"{BASE}/api/ai_task/publicTask?taskId={task_id}"
            except Exception:
                pass

        return {
            "taskId": task_id, "markdown": display, "meta": meta,
            "share_url": share_url, "engine": "infini", "model": MODEL,
            "elapsed_ms": int((asyncio.get_event_loop().time() - started) * 1000),
        }


async def run_analysis(question: str, task_text: str, emit) -> dict:
    return await run_task(task_text, emit, purpose="analyze")


async def run_deepen(deepen_text: str) -> str:
    r = await run_task(deepen_text, None, purpose="deepen", timeout=min(TIMEOUT, 200))
    return r.get("markdown") or "（深化未返回内容，请重试）"
