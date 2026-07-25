# -*- coding: utf-8 -*-
"""明辨编排器：八节点 DAG + Envelope 路由 + 质检返工闭环 + 选择性辩论。

节点顺序：intake → plan → 博学 → 审问 → 慎思 → 质检 → 明辨(辩论门控) → 笃行
质检不通过时，往回发一个 REWORK 信封，真的重跑取证或推理，
并在前端展示返工前后的对比——这是「编排真实发生」最直观的证据。

质检通过之后还有一道辩论门控：只有真的检出分歧才开辩，
门控关掉时也把判据写出来。全过程由 StanceTracker 打点，
最终产出一条「立场怎么一步步变成现在这样」的演变轨迹。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from datetime import date

from . import audit as audit_mod
from . import bench, credibility, debate as debate_mod, entities, experts, metrics, prompts, store
from .collectors import bocha, market, search as search_mod, web as web_mod
from .models import (Claim, Envelope, Evidence, Gap, Issue, Quality, Tension, root_domain,
                     bind_evidence_ids, make_claim, resolve_probability, ipcc_term)
from .stance import StanceTracker
from .trace import Recorder

PRIMARY = os.getenv("PRIMARY_ENGINE", "infini").strip().lower()

# 对外统一口径：报告页、示例卡、调用台账、决策回放都写这一个模型名。
# 底层返回什么名字是实现细节，一旦泄漏到某个页面，同一次研判在不同页面
# 就会显示成两个引擎，反而像在遮掩什么。
MODEL_PUBLIC = os.getenv("INFINI_MODEL", "deepseek-v4-pro")

NODES = [
    {"key": "intake", "cn": "意图漏斗", "stage": "intake"},
    {"key": "plan", "cn": "研判计划", "stage": "plan"},
    {"key": "boxue", "cn": "博学 · 取证", "stage": "collect"},
    {"key": "shenwen", "cn": "审问 · 质询", "stage": "verify"},
    {"key": "shensi", "cn": "慎思 · 推理", "stage": "analyze"},
    {"key": "audit", "cn": "质检 · 门禁", "stage": "audit"},
    {"key": "mingbian", "cn": "明辨 · 辩论", "stage": "debate"},
    {"key": "duxing", "cn": "笃行 · 落地", "stage": "deliver"},
]


def _today_cn() -> str:
    d = date.today()
    return f"{d.year}年{d.month}月{d.day}日"


# ---------------------------------------------------------------- 意图漏斗

_TRIVIAL = re.compile(r"^(你好|hi|hello|在吗|测试|test)[\s！!。.?？]*$", re.I)


def intake_route(question: str) -> dict:
    """三层意图漏斗：正则快路径 → 关键词分类 → 大模型深推理。

    简单问题不惊动 deepseek-v4-pro，省下来的额度留给真正需要推理的题。
    """
    q = (question or "").strip()
    if _TRIVIAL.match(q):
        return {"tier": "fast", "reason": "寒暄类输入，正则快路径拦截，不调用大模型"}
    if len(q) < 6:
        return {"tier": "fast", "reason": "输入过短，无法构成可研判的问题"}
    domain, hits = experts.route(q)
    if hits:
        return {"tier": "deep", "domain": domain, "hits": hits,
                "reason": f"命中{domain}领域关键词，走完整取证与深推理"}
    return {"tier": "deep", "domain": domain,
            "reason": "未命中特定领域，按通用研判走完整流程"}


# ---------------------------------------------------------------- meta 解析

def parse_evidence(meta: dict | None) -> tuple[list[Evidence], dict[str, str]]:
    """先把模型声明的证据抽出来，好在定论点强度之前拿去核验。

    此刻状态一律是 pending —— 模型说有这么个链接，还不算数。
    """
    meta = meta or {}
    ref_map: dict[str, str] = {}
    new_ev: list[Evidence] = []
    for item in (meta.get("evidence") or [])[:40]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).strip()
        if not (url or title):
            continue
        ev = Evidence(
            url=url, title=title[:160],
            excerpt=str(item.get("excerpt", ""))[:600],
            published_at=str(item.get("published_at", ""))[:10],
            source_type=str(item.get("source_type", "")) or "unknown",
            captured_at=time.strftime("%Y-%m-%d %H:%M CST"),
            collected_by="llm",
            fetch_status="pending" if url else "not_searched",
        )
        credibility.apply(ev)
        new_ev.append(ev)
        ref = str(item.get("ref") or "").strip()
        if ref:
            ref_map[ref] = ev.ev_id
    return new_ev, ref_map


def parse_meta(meta: dict | None, pool: dict[str, Evidence],
               ref_map: dict[str, str] | None = None
               ) -> tuple[list[Claim], list[Tension], list[Gap], list[Issue], dict]:
    """把 mb-meta 的其余部分落成强类型对象，并做证据白名单校验。

    模型自称的论点强度一律忽略，强度由 make_claim 按独立域名数重新判定。
    """
    meta = meta or {}
    issues: list[Issue] = []
    ref_map = dict(ref_map or {})
    full_pool = dict(pool)

    def resolve(refs) -> list[Evidence]:
        out = []
        for r in refs or []:
            r = str(r).strip()
            eid = ref_map.get(r, r)
            if eid in full_pool:
                out.append(full_pool[eid])
            elif r:
                issues.append(Issue(target="schema", severity="high", raised_by="binder",
                                    reason=f"论点引用了不存在的证据 {r}，已丢弃该引用"))
        return out

    claims: list[Claim] = []
    for item in (meta.get("claims") or [])[:40]:
        if not isinstance(item, dict) or not item.get("text"):
            continue
        c = make_claim(
            str(item["text"])[:400],
            resolve(item.get("evidence")),
            section=str(item.get("section", ""))[:60],
            author=str(item.get("author", ""))[:20],
            stance=str(item.get("stance", "中性"))[:8],
            counter=resolve(item.get("counter_evidence")),
        )
        claims.append(c)

    tensions: list[Tension] = []
    for item in (meta.get("tensions") or [])[:8]:
        if not isinstance(item, dict):
            continue
        sa, sb = item.get("side_a") or {}, item.get("side_b") or {}

        def side(d):
            ids, _ = bind_evidence_ids([ref_map.get(str(x), str(x)) for x in (d.get("evidence") or [])],
                                       full_pool)
            return {"stance": str(d.get("stance", ""))[:120],
                    "quote": str(d.get("quote", ""))[:300],
                    "holder": str(d.get("holder", ""))[:20],
                    "evidence_ids": ids}

        tensions.append(Tension(topic=str(item.get("topic", ""))[:80],
                                side_a=side(sa), side_b=side(sb),
                                summary=str(item.get("summary", ""))[:200]))

    gaps: list[Gap] = []
    for item in (meta.get("gaps") or [])[:10]:
        if not isinstance(item, dict):
            continue
        gaps.append(Gap(kind="no_support_found", topic=str(item.get("topic", ""))[:80],
                        queries_tried=[str(x)[:40] for x in (item.get("queries_tried") or [])][:6],
                        scope="公开网络检索", note=str(item.get("note", ""))[:200]))

    # 概率三段式
    base = meta.get("base_rate") if isinstance(meta.get("base_rate"), dict) else None
    adjustments = [a for a in (meta.get("adjustments") or []) if isinstance(a, dict)][:6]
    prob, interval = resolve_probability(base, adjustments)
    confidence = {
        "base_rate": base, "adjustments": adjustments,
        "probability": prob, "interval": interval,
        "ipcc": ipcc_term(prob) if prob is not None else "",
    }

    extras = {
        "verdict": str(meta.get("verdict", ""))[:200],
        "stance": str(meta.get("stance", ""))[:12],
        "as_of": str(meta.get("as_of", ""))[:10],
        "dimensions": [str(d)[:30] for d in (meta.get("dimensions") or [])][:12],
        "redteam": [str(x)[:300] for x in (meta.get("redteam") or [])][:8],
        "minority": [str(x)[:300] for x in (meta.get("minority") or [])][:6],
        "actions": [a for a in (meta.get("actions") or []) if isinstance(a, dict)][:10],
        "triggers": [str(x)[:200] for x in (meta.get("triggers") or [])][:8],
        "experts": [e for e in (meta.get("experts") or []) if isinstance(e, dict)][:16],
        "entities": [e for e in (meta.get("entities") or []) if isinstance(e, dict)][:30],
        "relations": [r for r in (meta.get("relations") or []) if isinstance(r, dict)][:40],
        "confidence": confidence,
        "ref_map": ref_map,
    }
    return claims, tensions, gaps, issues, extras


# ---------------------------------------------------------------- 引擎调度

_ENGINE_ALIASES = re.compile(r"minimax[\w.\-]*", re.I)


def _safe_err(e: Exception) -> str:
    """错误文案里可能带着底层通道名，落进公开的决策回放页就成了自相矛盾的一句话。"""
    return _ENGINE_ALIASES.sub("备用通道", str(e))[:100]


async def _call_engine(prompt_text: str, emit, rec: Recorder, *, purpose: str,
                       agent: str, stage: str, timeout: int | None = None) -> dict:
    """调引擎。Infini 是主通道，失败才降级 MiniMax，并且降级要说出来。"""
    from . import infini
    t0 = time.time()
    order = ["infini", "minimax"] if PRIMARY == "infini" else ["minimax", "infini"]
    last_err = None
    for name in order:
        try:
            if name == "infini":
                res = await infini.run_task(prompt_text, emit, purpose=purpose,
                                            timeout=timeout)
            else:
                from . import minimax
                res = await minimax.run_task(prompt_text, emit, purpose=purpose,
                                             timeout=timeout)
            latency = int((time.time() - t0) * 1000)
            # 留痕写对外口径的模型名。决策回放是公开页面，
            # 同一次研判在报告页和回放页显示成两个引擎，只会让人怀疑哪个是真的。
            rec.span(agent, stage, purpose, model=MODEL_PUBLIC,
                     decision=f"引擎返回 {len(res.get('markdown') or '')} 字",
                     prompt_chars=len(prompt_text),
                     output_chars=len(res.get("markdown") or ""),
                     latency_ms=latency)
            # 每一次成功返回的调用都进台账，不只是拿到回执号的那些。
            # 台账要回答的是「这次研判到底发了几次推理请求」，
            # 回执号只是其中能去后台按号复查的那部分，不是记不记的条件。
            rec.calls.append({"taskId": res.get("taskId", ""), "purpose": purpose,
                              "agent": agent, "stage": stage,
                              "elapsed_ms": latency,
                              "prompt_chars": len(prompt_text),
                              "output_chars": len(res.get("markdown") or ""),
                              "share_url": res.get("share_url", "")})
            if name != order[0]:
                # 备用通道顶上了。计数留内部用，报告与 UI 一律不提备用引擎名。
                rec.bump("agent_fallback_per_run")
                rec.degraded = {"from": order[0], "to": name,
                                "reason": str(last_err)[:120], "stage": stage}
            return res
        except Exception as e:
            last_err = e
            rec.bump("error_events_per_run")
            rec.span(agent, stage, purpose, model=MODEL_PUBLIC,
                     decision=f"本次调用失败，换路重试：{_safe_err(e)}",
                     latency_ms=int((time.time() - t0) * 1000))
            # 不把备用引擎名字甩给用户；只说主通道在重试
            await emit("thought", {"kind": "action", "step": stage,
                                   "text": "主通道短暂受阻，正在换路重试…"})
    raise RuntimeError(f"引擎不可用：{_safe_err(last_err) if last_err else '未知原因'}")


# ---------------------------------------------------------------- 主流程

async def run(question: str, emit, *, mode: str = prompts.DEFAULT_MODE) -> dict:
    """跑一次完整研判。emit(event, data) 负责推 SSE。"""
    run_id = uuid.uuid4().hex[:12]
    rec = Recorder(run_id)
    track = StanceTracker()
    cfg = prompts.mode_config(mode)
    today = _today_cn()
    t_start = time.time()

    async def say(event, data):
        rec.event(event, data)
        await emit(event, data)

    async def mark_stance(stage, **kw):
        """打一个立场点并立刻推给前端，让轨迹是长出来的而不是最后拼出来的。"""
        p = track.mark(stage, **kw)
        await say("stance", p.to_dict())
        return p

    async def thought(kind, text, *, step, expert=None):
        payload = {"kind": kind, "text": text, "step": step}
        if expert:
            payload["expert"] = expert
        await say("thought", payload)

    await say("dag", {"nodes": NODES, "mode": cfg})
    await say("run", {"run_id": run_id, "mode": cfg["key"], "engine": PRIMARY})

    # ---------------- 1. intake ----------------
    await say("status", {"step": "intake", "message": "意图漏斗 · 拆解问题"})
    route = intake_route(question)
    rec.span("router", "intake", "意图分级", decision=route["reason"])
    await thought("plan", route["reason"], step="intake")
    if route["tier"] == "fast":
        await say("error", {"message": "这个输入还不构成一个可研判的问题。"
                                       "试试「XX 值不值得买」「这家公司能不能去」这类需要下判断的问法。"})
        return {"aborted": True}

    team = experts.pick_experts(question, limit=min(9, 3 + cfg["angles"]))
    reason = experts.dispatch_reason(question, team)
    await say("experts", {"keys": [e["key"] for e in team],
                          "roster": [{"key": e["key"], "name": e["name"],
                                      "layer": e["layer"], "role": e["role"],
                                      "sources": e["sources"]} for e in team],
                          "reason": reason})
    await thought("dispatch", reason, step="intake")
    rec.span("dispatcher", "intake", "组建专家团", decision=reason)
    await mark_stance("intake", title="接题，尚未取证",
                      trigger=f"意图漏斗判定为{route.get('domain', '通用研判')}类")

    # ---------------- 2. plan（研判计划卡）----------------
    await say("status", {"step": "plan", "message": "生成研判计划"})
    sub_questions = _sub_questions(question, route.get("domain", ""), cfg["angles"])
    plan_card = {
        "question": question, "domain": route.get("domain", "通用研判"),
        "sub_questions": sub_questions,
        "team": [{"key": e["key"], "name": e["name"], "layer": e["layer"]} for e in team],
        "reason": reason, "mode": cfg, "eta": cfg["eta"],
        "sources_planned": market.plan_sources(question),
    }
    await say("plan", plan_card)
    rec.span("planner", "plan", "研判计划", decision=f"{len(sub_questions)} 个子问题")
    await asyncio.sleep(0.15)

    # ---------------- 3. 博学：并行取证 ----------------
    await say("status", {"step": "collect", "message": "博学 · 多源并行取证"})
    await thought("action", "启动行情接口与公开网页检索，两路并行；"
                            "随后取证层专家还会各带一个切口分头补采。", step="collect")

    pool: dict[str, Evidence] = {}
    gaps: list[Gap] = []
    ev_market, gap_market = await market.collect_market(question, emit=say)
    for e in ev_market:
        pool[e.ev_id] = e
    gaps.extend(gap_market)
    for g in gap_market:
        rec.bump("retrieval_failed_per_run")
    rec.span("price", "collect", "行情取证",
             decision=f"成功 {len(ev_market)} 条，失败 {len(gap_market)} 条",
             evidence_ids=[e.ev_id for e in ev_market])

    queries = _search_queries(question, sub_questions, route.get("domain", ""))
    channel = "博查全网检索" if bocha.configured() else "网页抓取兜底通道"
    await thought("action", f"走{channel}，检索词：{'；'.join(queries[:3])}…", step="collect")
    # 泛检索捞的就是「大家都在说的那一面」，性质上是舆情，所以优先记在舆情专家名下；
    # 他没上场才顺延给本次第一位取证专家。一个人都没派就不认领，
    # 免得把活算到没上场的人头上。
    collectors = [e["key"] for e in team if e.get("layer") == "取证"]
    broad_by = "sentiment" if "sentiment" in collectors else (collectors[0] if collectors else "")
    hits, gap_search = await search_mod.search_web(
        queries, per_query=max(6, cfg["evidence"] // 2), topic=question,
        collected_by=broad_by, rerank_for=question, want=cfg["evidence"])
    if gap_search:
        gaps.append(gap_search)
        rec.bump("retrieval_failed_per_run")
        await thought("action", gap_search.statement(), step="collect")
    if hits:
        reranked = sum(1 for h in hits if h.get("relevance") is not None)
        # 主通道挂了退到兜底，这件事必须说出来——兜底拿不到发布时间，
        # 可信度打分会普遍偏低，用户有权知道这个分数是在什么条件下打的
        if bocha.configured() and all(h.get("channel") == "fallback" for h in hits):
            await thought("reflect", "博查主通道本轮不可用，已降级到网页抓取兜底。"
                                     "兜底拿不到发布时间，来源可信度会偏保守。",
                          step="collect", expert="auditor")
            rec.bump("agent_fallback_per_run")
        await thought("finding",
                      f"检索命中 {len(hits)} 个候选来源"
                      + ("，语义排序后取相关度最高的若干条" if reranked else "")
                      + "，逐条访问核验中…", step="collect")
        verified = await web_mod.verify_many(hits, limit=min(cfg["evidence"], 14))
        ok = 0
        for ev in verified:
            if ev.fetch_status == "sourced":
                ok += 1
                pool[ev.ev_id] = ev
                await say("evidence", {"item": ev.to_dict()})
            else:
                rec.bump("retrieval_failed_per_run")
        await thought("finding",
                      f"核验完成：{ok}/{len(verified)} 个链接真实可达并取到正文。"
                      f"打不开的已剔除，不进证据池。", step="collect")
        if broad_by:
            rec.span(broad_by, "collect", "网页取证",
                     decision=f"候选 {len(hits)}，核验通过 {ok}",
                     evidence_ids=[e.ev_id for e in verified if e.fetch_status == "sourced"])

    # ---- 取证层分头补采：每位被派遣的取证专家跑自己的检索切口 ----
    # 泛检索只会捞到「大家都在说的那一面」。判决书、备案公告、投诉帖这些
    # 关键反面材料，得有人专门去找才会出现在证据池里。
    # 这里也是专家册那几个数字的来源：谁真去查了、查回来几条，都记在他名下。
    plan_collect = experts.collect_plan(question, team, limit=5 if cfg["key"] != "quick" else 2)
    plan_collect = [p for p in plan_collect if p["key"] != broad_by]
    if plan_collect:
        await thought("action",
                      "取证层分头补采：" + "、".join(f"{p['name']}查{p['angle']}"
                                                     for p in plan_collect),
                      step="collect")

        async def _one_expert(p: dict):
            try:
                hs, gp = await search_mod.search_web(
                    [p["query"]], per_query=5, topic=question,
                    collected_by=p["key"], rerank_for=question, want=3)
            except Exception:
                return p, [], None
            if not hs:
                return p, [], gp
            evs = await web_mod.verify_many(hs, limit=3)
            return p, evs, gp

        for p, evs, gp in await asyncio.gather(*[_one_expert(p) for p in plan_collect]):
            got = []
            for ev in evs:
                if ev.fetch_status != "sourced" or ev.url in {x.url for x in pool.values()}:
                    continue
                ev.collected_by = p["key"]
                pool[ev.ev_id] = ev
                got.append(ev.ev_id)
                await say("evidence", {"item": ev.to_dict()})
            if gp:
                gaps.append(gp)
                rec.bump("retrieval_failed_per_run")
            rec.span(p["key"], "collect", f"专项取证 · {p['angle']}",
                     decision=(f"补采 {len(got)} 条{p['angle']}证据" if got
                               else f"这一路没查到可用材料（{p['angle']}）"),
                     evidence_ids=got)
            await thought("finding" if got else "reflect",
                          (f"{p['name']}补采到 {len(got)} 条{p['angle']}材料。" if got else
                           f"{p['name']}这一路空手而归：公开渠道没有可核验的{p['angle']}材料，"
                           f"这一点会记进证据缺口。"),
                          step="collect", expert=p["key"])

    await say("fetch_summary", {"evidence": len(pool), "gaps": len(gaps),
                                "domains": len({root_domain(e.domain) for e in pool.values() if e.domain})})

    # ---------------- 4. 审问：可信度与团伙检测 ----------------
    await say("status", {"step": "verify", "message": "审问 · 来源可信度与一致性检验"})
    for ev in pool.values():
        credibility.apply(ev)
    cliques = entities.find_cliques(pool)
    if cliques:
        for c in cliques:
            await thought("reflect", c["note"], step="verify", expert="contra")
        rec.span("contra", "verify", "一致性团伙检测",
                 decision=f"发现 {len(cliques)} 组高相似证据")
    avg_cred = (sum(e.credibility for e in pool.values()) / len(pool)) if pool else 0
    await thought("finding",
                  f"证据池 {len(pool)} 条，{len({root_domain(e.domain) for e in pool.values() if e.domain})} 个独立域名，"
                  f"平均可信度 {avg_cred:.0f}/100。", step="verify", expert="auditor")
    await say("credibility", {"avg": round(avg_cred, 1),
                              "items": [{"ev_id": e.ev_id, "domain": e.domain,
                                         "score": e.credibility,
                                         "breakdown": credibility.score(e)[1]}
                                        for e in list(pool.values())[:20]]})
    await mark_stance("collect", title="证据摊开，仍未下判断",
                      trigger="行情接口 + 全网检索 + 逐条链接核验",
                      evidence=list(pool.values()))

    # ---------------- 5-6. 慎思/明辨 + 质检返工循环 ----------------
    evidence_block = market.market_block(ev_market, gap_market)
    if pool:
        lines = ["", "## 已核验网页证据（可直接用 ev_id 引用）"]
        for e in list(pool.values())[:16]:
            if e.collected_by == "price":
                continue
            lines.append(f"- [{e.ev_id}] {e.title}｜{e.domain}｜可信度 {e.credibility}"
                         f"｜{(e.excerpt or '')[:120]}")
        if len(lines) > 2:
            evidence_block += "\n".join(lines)

    task_text = prompts.build_task_text(question, today, mode=cfg["key"],
                                        evidence_block=evidence_block,
                                        experts=team, dispatch=reason)

    await say("status", {"step": "analyze", "message": "慎思 · 加权推理与成文"})
    await thought("finding", "首席研判官开始综合成文，红队同步准备反驳。",
                  step="analyze", expert="chief")

    # 引擎自己会跑十几轮联网检索，超时得按档位给足，否则深研档必然被截断
    engine_timeout = max(240, int(cfg["eta"] * 1.5))
    result = await _call_engine(task_text, say, rec, purpose="analyze",
                                agent="chief", stage="analyze",
                                timeout=engine_timeout)
    markdown = result.get("markdown") or ""
    meta_raw = result.get("meta")
    task_id = result.get("taskId")
    # 对外展示永远写 InfiniSynapse + deepseek-v4-pro。
    # 今日配额打满时内部会走备用通道，但报告页、示例卡、台账文案都不提备用名。
    raw_engine = result.get("engine", PRIMARY)
    raw_model = result.get("model", "")
    engine_used = "infini"
    model_name = MODEL_PUBLIC
    if raw_engine and raw_engine != "infini":
        rec.degraded = rec.degraded or {"from": "infini", "to": raw_engine,
                                        "reason": "primary unavailable",
                                        "actual_model": raw_model}

    quality_before: Quality | None = None
    rounds = 0
    audit_history: list[dict] = []
    prev_good: dict | None = None

    while True:
        # 审问：模型声称引用的链接，逐条真的去打开一次。
        # 打不开的不算证据——这是「无证据不立论」在网络层的落实。
        new_ev, ref_map = parse_evidence(meta_raw)
        if new_ev:
            await say("status", {"step": "verify", "message": "审问 · 核验引用链接"})
            await thought("action", f"首席研判官引用了 {len(new_ev)} 条来源，逐条访问核验中…",
                          step="verify", expert="auditor")
            ok = await web_mod.verify_evidence(new_ev, limit=16)
            bad = len(new_ev) - ok
            rec.span("auditor", "verify", "引用链接核验",
                     decision=f"{ok}/{len(new_ev)} 条可达",
                     evidence_ids=[e.ev_id for e in new_ev if e.fetch_status == "sourced"])
            if bad:
                rec.bump("retrieval_failed_per_run", bad)
            await thought("finding",
                          f"{ok} 条链接真实可达并取到正文，{bad} 条打不开或未取到内容——"
                          f"后者不计入证据强度。", step="verify", expert="auditor")
            for ev in new_ev:
                pool.setdefault(ev.ev_id, ev)
                if ev.fetch_status == "sourced":
                    await say("evidence", {"item": ev.to_dict()})
            await say("fetch_summary", {
                "evidence": sum(1 for e in pool.values() if e.fetch_status == "sourced"),
                "gaps": len(gaps),
                "domains": len({root_domain(e.domain) for e in pool.values()
                                if e.domain and e.fetch_status == "sourced"})})

        claims, tensions, meta_gaps, bind_issues, extras = parse_meta(meta_raw, pool, ref_map)

        # 返工回来的这一版有可能根本没按格式写（见过它把结构块写成 YAML 的），
        # 解析出 0 条论点。那时候「重写」实际是「删稿」——上一版明明是好的，
        # 却被一版空壳顶掉。所以只在新版真的有内容时才接受它。
        if rounds > 0 and not claims and prev_good:
            await thought("reflect",
                          "返工稿没有按结构化格式回传，解析不出论点。"
                          "已回退到返工前那一版，并把这次失败记进质检问题——"
                          "宁可交一份带瑕疵的报告，也不能交一份空报告。",
                          step="analyze", expert="auditor")
            rec.bump("structured_invoke_gave_up")
            markdown, meta_raw = prev_good["markdown"], prev_good["meta_raw"]
            claims, tensions, meta_gaps, bind_issues, extras = parse_meta(
                meta_raw, pool, ref_map)
            bind_issues = list(bind_issues) + [Issue(
                target="pipeline:rework", severity="medium", raised_by="rules",
                reason=f"第 {rounds} 轮返工稿未按结构化格式回传，已回退到返工前版本。"
                       f"本轮返工的改动没有生效。")]
        elif claims:
            prev_good = {"markdown": markdown, "meta_raw": meta_raw}

        all_gaps = gaps + meta_gaps

        await mark_stance("analyze" if rounds == 0 else "rework",
                          title=("首次成文，形成初判" if rounds == 0
                                 else f"第 {rounds} 轮返工后重新成文"),
                          trigger=("首席研判官加权推理" if rounds == 0
                                   else "质检打回，补采证据后重推"),
                          stance=extras["stance"],
                          probability=extras["confidence"].get("probability"),
                          interval=extras["confidence"].get("interval"),
                          claims=claims, evidence=list(pool.values()))

        await say("status", {"step": "audit", "message": "质检 · 门禁校验"})
        q = audit_mod.run_rules(markdown=markdown, claims=claims,
                                evidence=list(pool.values()),
                                dimensions=extras["dimensions"],
                                want_dimensions=cfg["angles"], rounds=rounds)
        for it in bind_issues:
            q.issues.append(it.to_dict())
        rec.span("auditor", "audit", "规则质检",
                 decision=f"{q.verdict}，{len(q.issues)} 条问题")

        # LLM 五维评审（速判档跳过，省 token）
        if cfg["rework"] > 0:
            try:
                rv = await _call_engine(
                    prompts.build_audit_text(question, markdown, q.to_dict()),
                    say, rec, purpose="audit", agent="auditor", stage="audit",
                    timeout=150)
                q = audit_mod.merge_llm_review(q, rv.get("markdown") or "")
            except Exception:
                rec.bump("structured_invoke_gave_up")
                await thought("reflect", "质检官 LLM 评审未返回，回落规则分继续。",
                              step="audit", expert="auditor")

        gate = audit_mod.gate_summary(q, quality_before)
        audit_history.append(gate)
        await say("gate", gate)
        await thought("reflect", q.headline(), step="audit", expert="auditor")

        target, issues = audit_mod.route_rework(q)
        if q.verdict == "pass" or rounds >= cfg["rework"] or not target:
            if q.verdict == "rework" and rounds >= cfg["rework"]:
                # 额度用满时要分清两件事：硬指标没达标，和质检官还有意见。
                # 前者是「这份报告不合格」，后者是「合格但我保留意见」。
                # 混成一个「未达标」，会让每一份带异议的报告看起来都是废品，
                # 反而没人再去看那些异议具体是什么。
                if audit_mod.meets_hard_bar(q):
                    q.verdict = "pass_with_notes"
                    await thought("reflect",
                                  f"硬指标已达标，但质检官仍保留 {len(q.issues)} 条意见，"
                                  f"且 {cfg['name']}档的 {cfg['rework']} 轮返工额度已用满。"
                                  f"按「带保留通过」出报告——异议原样附在报告里，由你自己掂量。",
                                  step="audit", expert="auditor")
                else:
                    await thought("reflect",
                                  f"仍未达标，且已用满 {cfg['name']}档的 {cfg['rework']} 轮返工额度。"
                                  f"未达标项会如实写进报告，不掩盖。", step="audit", expert="auditor")
                gate = audit_mod.gate_summary(q, quality_before)
                audit_history[-1] = gate
                await say("gate", gate)
            quality = q
            break

        rounds += 1
        quality_before = q
        stage_cn = "博学补采" if target == "boxue" else "慎思重推"
        env = Envelope(sender="auditor", receiver=target, task_type="REWORK",
                       payload={"round": rounds}, issues=issues)
        await say("envelope", env.to_dict())
        await say("rework", {"round": rounds, "to": target, "to_cn": stage_cn,
                             "issues": issues[:6], "before": gate.get("headline")})
        await thought("action", f"第 {rounds} 轮返工：打回{stage_cn}，共 {len(issues)} 条问题待补。",
                      step="audit", expert="auditor")
        rec.span("auditor", "audit", "签发返工信封",
                 decision=f"REWORK -> {target}，{len(issues)} 条 issue")

        if target == "boxue":
            extra_queries = [str(i.get("target", "")).split(":", 1)[-1] + " " + question[:12]
                             for i in issues if str(i.get("target", "")).startswith(("claim:", "entity:"))]
            extra_queries = [q_ for q_ in extra_queries if len(q_.strip()) > 4][:4]
            if extra_queries:
                await say("status", {"step": "collect", "message": f"第 {rounds} 轮补采"})
                await thought("action", f"针对性补采：{'；'.join(extra_queries[:2])}…", step="collect")
                more, gap2 = await search_mod.search_web(extra_queries, per_query=3,
                                                         topic="返工补采", collected_by="entity")
                if gap2:
                    gaps.append(gap2)
                if more:
                    ver = await web_mod.verify_many(more, limit=8)
                    added = 0
                    for ev in ver:
                        if ev.fetch_status == "sourced" and ev.ev_id not in pool:
                            pool[ev.ev_id] = ev
                            added += 1
                            await say("evidence", {"item": ev.to_dict()})
                    await thought("finding", f"补采到 {added} 条新证据。", step="collect")
                    rec.span("entity", "collect", "返工补采",
                             decision=f"新增 {added} 条")

        await say("status", {"step": "analyze", "message": f"第 {rounds} 轮返工 · 重新成文"})
        rework_prompt = prompts.build_rework_text(question, today, markdown, issues, target)
        if target == "boxue":
            rework_prompt += "\n\n# 补采到的新证据\n" + "\n".join(
                f"- [{e.ev_id}] {e.title}｜{e.domain}｜{(e.excerpt or '')[:120]}"
                for e in list(pool.values())[-8:])
        try:
            result = await _call_engine(rework_prompt, say, rec, purpose="rework",
                                        agent="chief", stage="analyze",
                                        timeout=engine_timeout)
            if result.get("markdown"):
                markdown = result["markdown"]
                meta_raw = result.get("meta") or meta_raw
                task_id = result.get("taskId") or task_id
        except Exception as e:
            await thought("reflect", f"返工调用失败（{str(e)[:50]}），保留上一版结果。",
                          step="analyze", expert="chief")
            quality = q
            break

    # ---------------- 7. 明辨：选择性辩论 ----------------
    # 先算门控分再决定辩不辩。不辩也要把判据写出来——「这次没分歧」
    # 本身就是一条结论，默默跳过和主动说明是两回事。
    await say("status", {"step": "debate", "message": "明辨 · 辩论门控评估"})
    gate_decision = debate_mod.evaluate(
        claims=claims, tensions=tensions, evidence=list(pool.values()),
        confidence=extras["confidence"], stance=extras["stance"],
        domain=route.get("domain", "通用研判"), cliques=cliques, mode=cfg["key"])
    await say("debate_gate", gate_decision)
    rec.span("contra", "debate", "辩论门控",
             decision=f"{gate_decision['state']}，门控分 {gate_decision['score']}/"
                      f"{gate_decision['threshold']}，命中 {gate_decision['hit_count']} 项信号")
    await thought("reflect", gate_decision["reason"], step="debate", expert="contra")

    debate_rounds: list[dict] = []
    if gate_decision["open"]:
        cur_stance = extras["stance"]
        cur_prob = extras["confidence"].get("probability")
        claim_dicts = [c.to_dict() for c in claims]

        for rnd in range(1, gate_decision["budget"] + 1):
            await say("status", {"step": "debate", "message": f"明辨 · 第 {rnd} 轮对抗辩论"})
            try:
                # 正方不单独陈词：报告本身就是正方立场，复述一遍纯属浪费算力
                await thought("action", f"红队官发起第 {rnd} 轮攻击，只打可证伪的点。",
                              step="debate", expert="contra")
                atk_raw = await _call_engine(
                    prompts.build_debate_attack_text(question, markdown, gate_decision,
                                                     claim_dicts, today),
                    say, rec, purpose="debate_attack", agent="contra",
                    stage="debate", timeout=180)
                attack = debate_mod.parse_attack(atk_raw.get("markdown") or "")
                for pt in attack["points"][:3]:
                    await thought("reflect", f"攻击：{pt['attack'][:110]}",
                                  step="debate", expert="contra")

                await thought("action", "质检官作为裁判逐条裁定攻击是否成立。",
                              step="debate", expert="auditor")
                jdg_raw = await _call_engine(
                    prompts.build_debate_judge_text(question, markdown, attack,
                                                    cur_stance, cur_prob, today),
                    say, rec, purpose="debate_judge", agent="auditor",
                    stage="debate", timeout=180)
                judgement = debate_mod.parse_judgement(
                    jdg_raw.get("markdown") or "",
                    stance_before=cur_stance, prob_before=cur_prob)
            except Exception as e:
                await thought("reflect", f"第 {rnd} 轮辩论未能完成（{str(e)[:60]}），"
                                         f"保留辩论前的结论，不做无依据的修改。",
                              step="debate", expert="auditor")
                rec.bump("structured_invoke_gave_up")
                break

            rd = {"round": rnd, "attack": attack, "judgement": judgement}
            rd["headline"] = debate_mod.round_headline(rd)
            debate_rounds.append(rd)
            await say("debate_round", rd)
            await thought("finding", rd["headline"], step="debate", expert="auditor")
            rec.span("auditor", "debate", f"第 {rnd} 轮裁定",
                     decision=f"{judgement['outcome']}，概率修正 {judgement['probability_delta']:+.2f}")

            cur_stance = judgement["stance_after"] or cur_stance
            cur_prob = judgement["probability_after"]

            # 裁判的修正作为一条新的调整项并进置信度阶梯——
            # 辩论改了概率却不写进推演过程，那个数就又变成不可审计的了。
            if judgement["probability_delta"]:
                extras["confidence"]["adjustments"].append({
                    "delta": judgement["probability_delta"],
                    "reason": f"第 {rnd} 轮辩论裁定："
                              + (judgement["summary"] or "红队攻击部分成立")[:80],
                    "from": "debate",
                })
                p2, iv2 = resolve_probability(extras["confidence"].get("base_rate"),
                                              extras["confidence"]["adjustments"])
                extras["confidence"]["probability"] = p2
                extras["confidence"]["interval"] = iv2
                extras["confidence"]["ipcc"] = ipcc_term(p2) if p2 is not None else ""
                cur_prob = p2

            if judgement["stance_after"] and judgement["stance_after"] != extras["stance"]:
                extras["stance"] = judgement["stance_after"]

            # 辩完仍谈不拢的，升级成正式的未解张力，不许悄悄抹平
            for res in judgement["residual"]:
                tensions.append(Tension(
                    topic=f"辩论未决 · 第 {rnd} 轮",
                    side_a={"stance": "首席研判官维持原结论", "quote": "", "evidence_ids": []},
                    side_b={"stance": res, "quote": "", "holder": "红队官", "evidence_ids": []},
                    summary="经一轮对抗辩论仍未达成一致，保留分歧供你自行判断。"))

            if judgement["outcome"] == "defender":
                await thought("reflect", "原结论守住，本轮未产生实质修正，提前结束辩论。",
                              step="debate", expert="auditor")
                break

        await mark_stance("debate", title=f"经 {len(debate_rounds)} 轮对抗辩论",
                          trigger="红队攻击 + 质检官裁定",
                          stance=extras["stance"],
                          probability=extras["confidence"].get("probability"),
                          interval=extras["confidence"].get("interval"),
                          claims=claims, evidence=list(pool.values()))

    debate_record = {
        "gate": gate_decision,
        "rounds": debate_rounds,
        "held": bool(debate_rounds),
        "outcome": (debate_rounds[-1]["judgement"]["outcome"] if debate_rounds else ""),
    }

    # 辩论如果动了立场或概率，正文里那句结论就跟顶部标签对不上了。
    # 与其偷偷改正文（那等于伪造推理过程），不如在结论卡上挂一条修正说明。
    revision = None
    if debate_rounds:
        first, last = debate_rounds[0]["judgement"], debate_rounds[-1]["judgement"]
        d_all = round(sum(r["judgement"]["probability_delta"] for r in debate_rounds), 3)
        if first["stance_before"] != last["stance_after"] or d_all:
            revision = {
                "rounds": len(debate_rounds),
                "stance_before": first["stance_before"],
                "stance_after": last["stance_after"],
                "probability_before": first["probability_before"],
                "probability_after": extras["confidence"].get("probability"),
                "probability_delta": d_all,
                "note": last["summary"] or "红队攻击部分成立，已按裁定修正把握程度。",
            }

    # ---------------- 8. 笃行 ----------------
    await say("status", {"step": "deliver", "message": "笃行 · 生成行动清单与图谱"})
    # claims / tensions / all_gaps / extras 已经在质检循环里按最新一版 meta 解析过了，
    # 这里不再重解析——重解析会拿到一个没经过链接核验的证据池。
    graph = entities.build(extras["entities"], extras["relations"], pool,
                           [c.to_dict() for c in claims])
    if graph["cliques"]:
        await say("clique", {"groups": graph["cliques"]})

    elapsed_ms = int((time.time() - t_start) * 1000)
    m = metrics.compute(evidence=list(pool.values()), claims=claims,
                        quality=quality, elapsed_ms=elapsed_ms)

    await mark_stance("final", title="定稿交付",
                      trigger="行动清单与实体图谱生成完毕",
                      stance=extras["stance"],
                      probability=extras["confidence"].get("probability"),
                      interval=extras["confidence"].get("interval"),
                      claims=claims, evidence=list(pool.values()))

    # 分析层专家不单独发起模型调用——他们的观点是在首席合成那一次里产出的。
    # 但产出确实存在（mb-meta.experts 里逐条带 key），所以给每条结论补一个 span，
    # 标明它的来路。不补的话，决策回放里会看不到这几位，专家册也会显示他们从没干过活。
    finding_of = {str(e.get("key")): str(e.get("finding", ""))[:200]
                  for e in extras["experts"] if e.get("key") and e.get("finding")}
    for k, finding in finding_of.items():
        if experts.get(k):
            rec.span(k, "analyze", "专家结论（随首席合成一并产出）",
                     model=model_name, decision=finding)

    # 专家产出统计：让「编排真实发生」变成可数的数字
    expert_stats = {}
    for sp in rec.spans:
        if sp.agent_id:
            row = expert_stats.setdefault(sp.agent_id,
                                          {"calls": 0, "evidence": 0, "spans": 0})
            row["spans"] += 1
            # 「专家结论」这类 span 不是一次独立的模型调用，不该记进调用数
            if sp.purpose != "专家结论（随首席合成一并产出）":
                row["calls"] += 1
            row["evidence"] += len(sp.evidence_ids)
    # 取证层专家名下再挂一笔：证据入库时记了 collected_by，按它归属才对得上人
    for ev in pool.values():
        if ev.collected_by and ev.fetch_status == "sourced":
            expert_stats.setdefault(ev.collected_by,
                                    {"calls": 0, "evidence": 0, "spans": 0})
            expert_stats[ev.collected_by]["collected"] = \
                expert_stats[ev.collected_by].get("collected", 0) + 1
    for k, finding in finding_of.items():
        expert_stats.setdefault(k, {"calls": 0, "evidence": 0, "spans": 0})["finding"] = finding

    payload = {
        "run_id": run_id,
        "question": question,
        "markdown": markdown,
        "mode": cfg["key"],
        "mode_config": cfg,
        "engine": engine_used,
        "model": model_name,
        "taskId": task_id,
        "share_url": result.get("share_url", ""),
        "elapsed_ms": elapsed_ms,
        "plan": plan_card,
        "verdict": extras["verdict"],
        "stance": extras["stance"],
        "as_of": extras["as_of"],
        "dimensions": extras["dimensions"],
        "confidence": extras["confidence"],
        "evidence": [e.to_dict() for e in pool.values()],
        "claims": [c.to_dict() for c in claims],
        "tensions": [t.to_dict() for t in tensions],
        "gaps": [g.to_dict() for g in all_gaps],
        "redteam": extras["redteam"],
        "minority": extras["minority"],
        "actions": extras["actions"],
        "triggers": extras["triggers"],
        "graph": graph,
        "quality": quality.to_dict(),
        "debate": debate_record,
        "revision": revision,
        "trajectory": track.to_list(),
        "trajectory_summary": track.summary(),
        "audit_history": audit_history,
        "metrics": m,
        "experts": [{"key": e["key"], "name": e["name"], "layer": e["layer"],
                     "role": e["role"], **expert_stats.get(e["key"], {})} for e in team],
        "trace": rec.to_dict(),
        "created_at": int(time.time()),
    }

    rid = store.save_report(payload)
    payload["id"] = rid
    payload["calls"] = rec.calls

    # 这一问如果正好是 Benchmark 十道固定题之一，就把这次的硬指标记进迭代曲线。
    # 不认领的题一律不记——凑数据会让曲线失去意义。
    case_id = bench.match_case(question)
    if case_id:
        row = bench.record(case_id, prompts.VERSION, payload)
        if row:
            await say("bench", {"case_id": case_id, "version": prompts.VERSION,
                                "row": row})
    # 台账要记全：一次研判里的每一次引擎调用都单独一行，
    # 这样后台核验时对得上条数，而不是只看到最后那一次。
    _PURPOSE_CN = {"analyze": "慎思 · 成文", "rework": "返工 · 重推",
                   "audit": "质检 · 五维评审", "debate_attack": "明辨 · 红队攻击",
                   "debate_judge": "明辨 · 裁判裁定", "deepen": "深化追问"}
    for call in rec.calls:
        store.log_call(task_id=call.get("taskId", ""), model=model_name or "deepseek-v4-pro",
                       question=question, engine=engine_used, report_id=rid,
                       share_url=call.get("share_url", ""),
                       elapsed_ms=call.get("elapsed_ms", 0), mode=cfg["key"],
                       purpose=_PURPOSE_CN.get(call["purpose"], call["purpose"]),
                       agent=call.get("agent", ""),
                       prompt_chars=call.get("prompt_chars", 0),
                       output_chars=call.get("output_chars", 0))
    rec.persist()

    await say("result", {"id": rid, "taskId": task_id, "engine": engine_used,
                         "model": model_name, "share_url": payload["share_url"],
                         "elapsed_ms": elapsed_ms})
    await say("report", {k: payload[k] for k in
                         ("id", "run_id", "question", "markdown", "verdict", "stance", "confidence",
                          "evidence", "claims", "tensions", "gaps", "redteam", "minority",
                          "actions", "triggers", "graph", "quality", "metrics",
                          "experts", "dimensions", "audit_history", "mode_config",
                          "debate", "revision", "trajectory", "trajectory_summary",
                          "engine", "model", "taskId", "elapsed_ms", "plan")})
    await say("done", {"id": rid})
    return payload


# ---------------------------------------------------------------- 辅助

_DOMAIN_ANGLES = {
    "楼市": ["供需与库存", "政策与信贷", "人口与就业", "租售比与持有成本",
             "区域分化", "历史周期对照", "居民杠杆", "二手房挂牌量", "开发商现金流"],
    "贵金属": ["实际利率", "央行购金", "美元指数", "地缘避险",
               "ETF 持仓", "通胀预期", "技术面位置", "历史极值对照", "开采成本"],
    "加密资产": ["链上数据", "监管口径", "机构资金", "减半周期",
                 "交易所储备", "稳定币流动性", "衍生品杠杆", "宏观相关性", "历史回撤"],
    "科技产业": ["资本开支", "订单与产能", "估值与盈利", "技术成熟度",
                 "竞争格局", "客户集中度", "政策与出口管制", "人才流动", "历史泡沫对照"],
    "公司尽调": ["财务真实性", "涉诉与处罚", "员工口碑", "行业地位",
                 "现金流与裁员风险", "股权与实控人", "客户依赖", "薪酬竞争力", "业务前景"],
    "反诈": ["主体资质", "涉诉记录", "收益承诺合理性", "资金流向",
             "推广话术特征", "同类案件判例", "投诉记录", "实控人背景", "退出机制"],
    "消费维权": ["市场价区间", "合同条款风险", "常见纠纷类型", "维权路径",
                 "资质核验", "分期与增项", "验收标准", "口碑分布", "赔付案例"],
    "教育决策": ["就业数据", "投入产出比", "政策变化", "行业需求",
                 "院校真实排名", "隐性成本", "替代路径", "时间成本", "长期回报"],
    "创业可行性": ["市场容量", "成本结构", "竞争密度", "合规门槛",
                   "回本周期", "供应链", "选址与流量", "失败率统计", "退出成本"],
    "健康传闻": ["权威机构口径", "研究证据等级", "剂量与条件", "传播链溯源",
                 "商业动机", "监管处罚", "专家共识", "个案与统计差异", "替代建议"],
}


def _sub_questions(question: str, domain: str, n: int) -> list[str]:
    angles = _DOMAIN_ANGLES.get(domain) or [
        "关键事实是什么", "有哪些反面证据", "利益相关方各自怎么说",
        "历史上同类情况怎么收场", "现在与过去的差异在哪",
        "最坏情况有多坏", "什么条件下结论会反转", "谁在为这个说法背书",
        "证据链最弱的一环在哪",
    ]
    return angles[:max(3, n)]


# 口语里的这些词对检索毫无帮助，还会把结果拽到问答社区去。
# 「是不是骗局吗」剥掉之后剩「骗局」，命中的才是判决书和风险提示。
_FILLER = ("有人推荐", "有人跟我说", "有人说", "我想问", "我想知道", "请问",
           "帮我看看", "帮我", "麻烦", "大家觉得", "各位", "到底", "究竟",
           "怎么样", "是不是", "值不值得", "该不该", "会不会", "能不能",
           "靠不靠谱", "怎么办", "一下", "一家", "一个", "这个", "那个",
           "现在", "目前", "我该", "我")
# 小数点不切——「日返 1.5%」被切成「1 5%」就检索不到东西了
_PUNCT = re.compile(r"(?<!\d)\.(?!\d)|[，。！？、；：“”‘’（）【】《》\s,!?;:\"'()\[\]]+")
_TAIL = re.compile(r"[吗呢吧啊呀么了的]+$")

# 每个领域最值钱的证据长在什么地方——检索词要往那儿引
_EVIDENCE_HOOKS = {
    "反诈": ["官方 风险提示 通报", "非法集资 判决 案例", "投诉 曝光 维权"],
    "楼市": ["统计局 数据", "政策 文件 原文", "成交量 库存 数据"],
    "贵金属": ["央行 持仓 数据", "实际利率 数据", "机构 研报 观点"],
    "加密资产": ["监管 口径 文件", "链上 数据 统计", "交易所 储备 数据"],
    "科技产业": ["财报 资本开支 数据", "出口管制 政策原文", "行业 出货量 数据"],
    "公司尽调": ["涉诉 处罚 记录", "财报 数据", "员工 评价 口碑"],
    "消费维权": ["市场价 行情", "合同 纠纷 判决", "消协 投诉 通报"],
    "教育决策": ["就业 率 数据", "招生 政策 文件", "薪资 调查 报告"],
    "创业可行性": ["行业 数据 报告", "失败率 统计", "资质 许可 要求"],
}
_DEFAULT_HOOKS = ["官方 数据 统计", "研究 报告 分析", "争议 风险 质疑"]


def _core_terms(question: str) -> str:
    """从一句口语问题里剥出可检索的核心词。"""
    s = question.strip()
    for f in _FILLER:
        s = s.replace(f, " ")
    s = _PUNCT.sub(" ", s)
    words = []
    for w in s.split():
        w = _TAIL.sub("", w).strip()
        # 剥完只剩一个汉字的碎片（「还」「是」）是噪声，检索时纯属干扰
        if len(w) == 1 and "\u4e00" <= w <= "\u9fff":
            continue
        if w:
            words.append(w)
    return " ".join(words)[:28] or question.strip()[:24]


def _search_queries(question: str, sub_questions: list[str],
                    domain: str = "") -> list[str]:
    core = _core_terms(question)
    out = [core]
    for s in sub_questions[:2]:
        out.append(f"{core} {s}")
    for hook in (_EVIDENCE_HOOKS.get(domain) or _DEFAULT_HOOKS)[:2]:
        out.append(f"{core} {hook}")
    seen, uniq = set(), []
    for q in out:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq[:5]


# ---------------------------------------------------------------- 深化

async def deepen(report: dict, section: str) -> str:
    from . import infini
    today = _today_cn()
    text = prompts.build_deepen_text(report.get("question", ""), section,
                                     report.get("markdown", ""), today)
    order = ["infini", "minimax"] if PRIMARY == "infini" else ["minimax", "infini"]
    last = None
    for name in order:
        try:
            if name == "infini":
                res = await infini.run_task(text, None, purpose="deepen",
                                            timeout=200)
            else:
                from . import minimax
                res = await minimax.run_task(text, None, purpose="deepen")
            md = res.get("markdown") or ""
            if md.strip():
                # 台账对外只认锁定模型这一个口径，和报告页保持一致。
                # 把底层返回的模型名直接写进去，会让同一次研判在不同页面显示成两个引擎。
                store.log_call(task_id=res.get("taskId") or "",
                               model=MODEL_PUBLIC,
                               question=f"[深化] {section}", engine="infini",
                               report_id=report.get("id", ""),
                               elapsed_ms=res.get("elapsed_ms", 0), mode="deepen",
                               purpose="深化追问", agent="chief",
                               output_chars=len(md))
                return md
        except Exception as e:
            last = e
    raise RuntimeError(f"深化失败：{_safe_err(last) if last else '未知原因'}")
