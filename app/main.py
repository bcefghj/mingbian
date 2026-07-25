# -*- coding: utf-8 -*-
"""司南 SINAN · Starlette 后端。"""
import os
import json
import asyncio
import html as _html

from .envload import load_env
load_env()

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse, StreamingResponse

from . import orchestrator, store, demos, infini, minimax
from .prompts import CAPABILITIES, EXPERT_ROSTER, build_deepen_text

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")


def _web(name):
    with open(os.path.join(WEB_DIR, name), encoding="utf-8") as f:
        return f.read()


async def healthz(request):
    return JSONResponse({"status": "ok", "app": "sinan",
                         "primary_engine": os.getenv("PRIMARY_ENGINE", "infini"),
                         "infini_configured": bool(os.getenv("INFINI_API_KEY")),
                         "minimax_configured": bool(os.getenv("MINIMAX_API_KEY"))})


async def index(request):
    return HTMLResponse(_web("index.html"))


async def capabilities(request):
    return JSONResponse({"capabilities": [{"name": n, "desc": d} for n, d in CAPABILITIES],
                         "experts": EXPERT_ROSTER})


async def api_demos(request):
    return JSONResponse({"demos": demos.list_demos()})


async def api_demo(request):
    d = demos.get_demo(request.path_params["did"])
    return JSONResponse(d) if d else JSONResponse({"error": "not found"}, status_code=404)


async def analyze(request):
    body = await request.json()
    question = (body.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "question required"}, status_code=400)
    q: asyncio.Queue = asyncio.Queue()

    async def emit(event, data):
        await q.put({"event": event, "data": data})

    async def worker():
        try:
            r = await orchestrator.run(question, emit)
            rid = store.save_report(question, r["markdown"], r.get("meta"), r.get("taskId"),
                                    r.get("share_url", ""), r.get("engine", "infini"))
            await emit("done", {"report_id": rid, "taskId": r.get("taskId"),
                                "share_url": r.get("share_url", ""), "engine": r.get("engine", "infini"),
                                "markdown": r["markdown"], "meta": r.get("meta")})
        except Exception as e:
            await emit("error", {"message": str(e)})
        finally:
            await q.put(None)

    async def stream():
        task = asyncio.create_task(worker())
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        await task

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def deepen(request):
    body = await request.json()
    rid = body.get("report_id")
    annotation = (body.get("annotation") or "").strip()
    section = body.get("section") or ""
    if not annotation:
        return JSONResponse({"error": "annotation required"}, status_code=400)
    rep = store.get_report(rid) or demos.get_demo(rid) or {}
    text = build_deepen_text(rep.get("question", ""), section, annotation, rep.get("markdown", ""))
    primary = (os.getenv("PRIMARY_ENGINE") or "minimax").lower()
    engines = [minimax, infini] if primary != "infini" else [infini, minimax]
    last = None
    for eng in engines:
        try:
            md = await eng.run_deepen(text)
            if md and str(md).strip():
                return JSONResponse({"markdown": md, "engine": getattr(eng, "__name__", "")})
        except Exception as e:
            last = e
            continue
    return JSONResponse({"error": f"深化失败：{last}"}, status_code=500)


async def share(request):
    b = await request.json()
    rid = store.save_report(b.get("question", ""), b.get("markdown", ""), b.get("meta"),
                            b.get("taskId"), b.get("share_url", ""), b.get("engine", "infini"))
    return JSONResponse({"id": rid})


async def api_report(request):
    rid = request.path_params["rid"]
    r = store.get_report(rid) or demos.get_demo(rid)
    return JSONResponse(r) if r else JSONResponse({"error": "not found"}, status_code=404)


async def report_page(request):
    return HTMLResponse(_web("report.html").replace("__REPORT_ID__", _html.escape(request.path_params["rid"])))


routes = [
    Route("/", index),
    Route("/healthz", healthz), Route("/api/health", healthz),
    Route("/api/capabilities", capabilities),
    Route("/api/demos", api_demos),
    Route("/api/demo/{did}", api_demo),
    Route("/api/analyze", analyze, methods=["POST"]),
    Route("/api/deepen", deepen, methods=["POST"]),
    Route("/api/share", share, methods=["POST"]),
    Route("/api/report/{rid}", api_report),
    Route("/report/{rid}", report_page),
]
app = Starlette(routes=routes)
