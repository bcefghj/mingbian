# -*- coding: utf-8 -*-
"""明辨 MINGBIAN · Starlette 后端。"""
from __future__ import annotations

import asyncio
import json
import os

from .envload import load_env
load_env()

from starlette.applications import Starlette
from starlette.responses import (FileResponse, HTMLResponse, JSONResponse,
                                 RedirectResponse, StreamingResponse)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import bench, debate, demos, experts, infini, metrics, pipeline, prompts, store
from .collectors import bocha
from .models import IPCC_SCALE, SOURCE_LABEL, STRENGTH_LABEL
from .stance import STAGE_CN
from .credibility import AUTHORITATIVE, BASE_SCORE

ROOT = os.path.dirname(os.path.dirname(__file__))
WEB_DIR = os.path.join(ROOT, "web")


def _web(name: str) -> str:
    with open(os.path.join(WEB_DIR, name), encoding="utf-8") as f:
        return f.read()


def _page(name: str):
    async def handler(request):
        return HTMLResponse(_web(name))
    return handler


# ---------------------------------------------------------------- 健康 / 元信息

async def healthz(request):
    return JSONResponse({
        "status": "ok", "app": "mingbian", "brand": prompts.BRAND,
        "primary_engine": pipeline.PRIMARY,
        "model": os.getenv("INFINI_MODEL", "deepseek-v4-pro"),
        "infini_configured": bool(os.getenv("INFINI_API_KEY")),
        "minimax_configured": bool(os.getenv("MINIMAX_API_KEY")),
    })


async def engine_probe(request):
    """真去 ping 一次 Infini 与博查，台账页顶部用。"""
    out = await infini.probe()
    out["search"] = bocha.status()
    return JSONResponse(out)


async def meta_info(request):
    return JSONResponse({
        "brand": prompts.BRAND, "tagline": prompts.TAGLINE,
        "stages": prompts.STAGES, "modes": prompts.MODES,
        "capabilities": prompts.CAPABILITIES,
        "experts": experts.roster_public(),
        "nodes": pipeline.NODES,
        "engine": {"primary": pipeline.PRIMARY,
                   "model": os.getenv("INFINI_MODEL", "deepseek-v4-pro")},
    })


async def methodology(request):
    return JSONResponse({
        "stages": prompts.STAGES,
        "ipcc": [{"term": t, "low": lo, "high": hi} for t, lo, hi in IPCC_SCALE],
        "strength": STRENGTH_LABEL,
        "source_types": {k: {"label": SOURCE_LABEL.get(k, k), "base": v}
                         for k, v in BASE_SCORE.items()},
        "authoritative": sorted(AUTHORITATIVE),
        "metrics": metrics.DEFINITIONS,
        "debate_gate": debate.SPEC,
        "trajectory": {
            "stages": STAGE_CN,
            "shift_kinds": {
                "init": "起点，尚未取证", "ground": "方向未变，证据底座变厚",
                "firm": "结论被加固（概率上调 ≥ 5 个点）",
                "soften": "结论被削弱（概率下调 ≥ 5 个点）",
                "reverse": "方向性掉头", "hold": "本步未改变结论",
            },
            "note": "轨迹上的每个数字都是运行时实测状态，变化说明由代码按前后差值生成，"
                    "不经模型润色。",
        },
        "grounding": [
            {"key": "sourced", "label": "已取证", "note": "链接可达且取到正文"},
            {"key": "pending", "label": "检索中", "note": "模型给出但尚未核验"},
            {"key": "retrieval_failed", "label": "检索失败", "note": "接口或页面不可达，可重试"},
            {"key": "no_support_found", "label": "未找到支持来源", "note": "检索器正常但零命中"},
            {"key": "not_searched", "label": "未检索", "note": "基于模型先验，请自行核实"},
        ],
    })


# ---------------------------------------------------------------- 案例

async def api_demos(request):
    return JSONResponse({"demos": demos.list_demos()})


async def api_demo(request):
    d = demos.get_demo(request.path_params["did"])
    return JSONResponse(d) if d else JSONResponse({"error": "not found"}, status_code=404)


# ---------------------------------------------------------------- 研判主流程

async def analyze(request):
    body = await request.json()
    question = (body.get("question") or "").strip()
    mode = (body.get("mode") or prompts.DEFAULT_MODE).strip()
    if not question:
        return JSONResponse({"error": "question required"}, status_code=400)

    q: asyncio.Queue = asyncio.Queue()
    seq = {"n": 0}

    async def emit(event, data):
        seq["n"] += 1
        await q.put({"id": seq["n"], "event": event, "data": data})

    async def worker():
        try:
            await pipeline.run(question, emit, mode=mode)
        except Exception as e:
            await emit("error", {"message": str(e)[:300]})
        finally:
            await q.put(None)

    async def stream():
        task = asyncio.create_task(worker())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=12)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if item is None:
                    break
                yield (f"id: {item['id']}\n"
                       f"data: {json.dumps(item, ensure_ascii=False)}\n\n")
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})


async def deepen(request):
    body = await request.json()
    rid = body.get("report_id") or ""
    section = (body.get("section") or body.get("annotation") or "").strip()
    if not section:
        return JSONResponse({"error": "section required"}, status_code=400)
    rep = store.get_report(rid) or demos.get_demo(rid)
    if not rep:
        return JSONResponse({"error": "report not found"}, status_code=404)
    try:
        md = await pipeline.deepen(rep, section)
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=500)
    # 写回报告，让它可以持续生长。
    # 预置 demo 存在 data/demos/，不在 reports/ 里——深化结果另存一份到 store，
    # 下次同一 id 优先读 store，评委深化过的内容不会丢。
    deepenings = rep.get("deepenings") or []
    deepenings.append({"section": section, "markdown": md, "ts": int(__import__("time").time())})
    if store.get_report(rid):
        store.update_report(rid, deepenings=deepenings)
    else:
        # demo 深化：把整份报告（含深化）落进 store，覆盖同 id
        patched = dict(rep)
        patched["deepenings"] = deepenings
        patched["id"] = rid
        store.save_report(patched, rid)
    return JSONResponse({"markdown": md, "count": len(deepenings)})


# ---------------------------------------------------------------- 报告与页面

async def api_report(request):
    rid = request.path_params["rid"]
    r = store.get_report(rid) or demos.get_demo(rid)
    return JSONResponse(r) if r else JSONResponse({"error": "not found"}, status_code=404)


async def api_reports(request):
    return JSONResponse({"reports": store.list_reports(60)})


async def api_ledger(request):
    return JSONResponse({"rows": store.read_ledger(200), "stats": store.ledger_stats()})


async def api_dashboard(request):
    return JSONResponse(metrics.global_snapshot())


async def api_experts(request):
    """名册 + 真实出场统计。专家册页面靠这个从静态卡片变成有据可查的记录。"""
    usage = metrics.expert_usage()
    return JSONResponse({
        "roster": experts.roster_public(),
        "always_on": experts.ALWAYS_ON,
        "runs": usage["runs"],
        "usage": usage["experts"],
    })


async def api_bench(request):
    return JSONResponse(bench.snapshot())


async def api_bench_run(request):
    """按 id 跑一道 Benchmark 题（前端会用普通 SSE 走 /api/analyze，这里只给题面）。"""
    return JSONResponse({"cases": bench.CASES})


async def api_review(request):
    b = await request.json()
    rid, cid = b.get("report_id", ""), b.get("claim_id", "")
    verdict = b.get("verdict", "")
    if verdict not in ("confirmed", "doubted", "rejected"):
        return JSONResponse({"error": "bad verdict"}, status_code=400)
    row = store.set_review(rid, cid, verdict, str(b.get("note", ""))[:300])
    return JSONResponse({"ok": True, "row": row, "stats": store.review_stats()})


async def api_reviews(request):
    return JSONResponse({"reviews": store.get_reviews(request.path_params["rid"]),
                         "stats": store.review_stats()})


async def api_watch(request):
    if request.method == "POST":
        b = await request.json()
        topic = (b.get("topic") or "").strip()
        if not topic:
            return JSONResponse({"error": "topic required"}, status_code=400)
        return JSONResponse({"item": store.add_watch(topic, b.get("report_id", ""))})
    return JSONResponse({"items": store.list_watch()})


async def api_alerts(request):
    path = os.path.join(ROOT, "reports", "_runs", "alerts.jsonl")
    rows = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        rows.append(json.loads(line))
        except Exception:
            pass
    return JSONResponse({"alerts": rows[-50:][::-1], "rules": __import__(
        "app.trace", fromlist=["ALERT_RULES"]).ALERT_RULES})


# 老版本的分享链接长这样：/sinan/app/report.html?id=xxxx
# 新版本改成了 /report/xxxx。两套形状对不上，得翻译而不是简单拼接。
_LEGACY_PAGES = {"report.html": "report", "trace.html": "trace",
                 "graph.html": "graph", "index.html": "",
                 "dashboard.html": "dashboard", "about.html": "about"}


def _hop(request, path: str) -> RedirectResponse:
    """带上反代前缀再跳。

    应用挂在 nginx 的 /mingbian/ 下面，后端自己并不知道这件事。
    直接 301 到 /report/xxx 会跳出反代前缀，落到根路径上 404。
    nginx 传 X-Forwarded-Prefix 过来，这里补回去。
    """
    prefix = (request.headers.get("x-forwarded-prefix") or "").rstrip("/")
    return RedirectResponse(f"{prefix}{path}" or "/", status_code=301)


def _legacy_target(page: str, rid: str) -> str:
    if page and rid:
        return f"/{page}/{rid}"
    return f"/{page}" if page else "/"


async def redirect_sinan(request):
    """旧路径 301，别让已经分享出去的链接烂掉。"""
    tail = request.path_params.get("tail", "").lstrip("/")
    if tail.startswith("app/"):
        tail = tail[4:]
    elif tail == "app":
        tail = ""

    page = _LEGACY_PAGES.get(tail)
    if page is not None:
        rid = request.query_params.get("id") or request.query_params.get("rid") or ""
        return _hop(request, _legacy_target(page, rid))
    return _hop(request, f"/{tail}")


async def legacy_page(request):
    """直接访问 /report.html?id=xxx 也认，同样翻译到新形状。"""
    page = _LEGACY_PAGES.get(request.url.path.lstrip("/"), "")
    rid = request.query_params.get("id") or request.query_params.get("rid") or ""
    return _hop(request, _legacy_target(page, rid))


async def favicon(request):
    p = os.path.join(WEB_DIR, "static", "favicon.svg")
    if os.path.exists(p):
        return FileResponse(p, media_type="image/svg+xml")
    return JSONResponse({}, status_code=404)


routes = [
    Route("/", _page("index.html")),
    Route("/report/{rid}", _page("report.html")),
    Route("/trace/{rid}", _page("trace.html")),
    Route("/graph/{rid}", _page("graph.html")),
    Route("/dashboard", _page("dashboard.html")),
    Route("/experts", _page("experts.html")),
    Route("/ledger", _page("ledger.html")),
    Route("/bench", _page("bench.html")),
    Route("/about", _page("about.html")),
    Route("/favicon.ico", favicon),

    Route("/healthz", healthz),
    Route("/api/health", healthz),
    Route("/api/engine", engine_probe),
    Route("/api/meta", meta_info),
    Route("/api/methodology", methodology),
    Route("/api/capabilities", meta_info),
    Route("/api/demos", api_demos),
    Route("/api/demo/{did}", api_demo),
    Route("/api/analyze", analyze, methods=["POST"]),
    Route("/api/deepen", deepen, methods=["POST"]),
    Route("/api/report/{rid}", api_report),
    Route("/api/reports", api_reports),
    Route("/api/ledger", api_ledger),
    Route("/api/dashboard", api_dashboard),
    Route("/api/experts", api_experts),
    Route("/api/bench", api_bench),
    Route("/api/bench/cases", api_bench_run),
    Route("/api/review", api_review, methods=["POST"]),
    Route("/api/reviews/{rid}", api_reviews),
    Route("/api/watch", api_watch, methods=["GET", "POST"]),
    Route("/api/alerts", api_alerts),

    Route("/sinan", redirect_sinan),
    Route("/sinan/{tail:path}", redirect_sinan),
    *[Route(f"/{n}", legacy_page) for n in _LEGACY_PAGES],

    Mount("/static", app=StaticFiles(directory=os.path.join(WEB_DIR, "static")), name="static"),
]

app = Starlette(routes=routes)
