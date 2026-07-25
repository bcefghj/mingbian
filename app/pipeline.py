# -*- coding: utf-8 -*-
"""明辨编排器：七节点 DAG + Envelope 路由 + 质检返工闭环。

节点顺序：intake → plan → 博学 → 审问 → 慎思/明辨 → 质检 → 笃行
质检不通过时，往回发一个 REWORK 信封，真的重跑取证或推理，
并在前端展示返工前后的对比——这是「编排真实发生」最直观的证据。
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
from . import credibility, entities, experts, metrics, prompts, store
from .collectors import bocha, market, search as search_mod, web as web_mod
from .models import (Claim, Envelope, Evidence, Gap, Issue, Quality, Tension, root_domain,
                     bind_evidence_ids, make_claim, resolve_probability, ipcc_term)
from .trace import Recorder

PRIMARY = os.getenv("PRIMARY_ENGINE", "infini").strip().lower()

NODES = [
    {"key": "intake", "cn": "意图漏斗", "stage": "intake"},
    {"key": "plan", "cn": "研判计划", "stage": "plan"},
    {"key": "boxue", "cn": "博学 · 取证", "stage": "collect"},
    {"key": "shenwen", "cn": "审问 · 质询", "stage": "verify"},
    {"key": "shensi", "cn": "慎思 · 推理", "stage": "analyze"},
    {"key": "audit", "cn": "质检 · 门禁", "stage": "audit"},
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
            rec.span(agent, stage, purpose, model=res.get("model", name),
                     decision=f"引擎 {name} 返回 {len(res.get('markdown') or '')} 字",
                     prompt_chars=len(prompt_text),
                     output_chars=len(res.get("markdown") or ""),
                     latency_ms=int((time.time() - t0) * 1000))
            if name != order[0]:
                rec.bump("agent_fallback_per_run")
                await emit("degraded", {"from": order[0], "to": name,
                                        "reason": str(last_err)[:120]})
            return res
        except Exception as e:
            last_err = e
            rec.bump("error_events_per_run")
            rec.span(agent, stage, purpose, model=name,
                     decision=f"失败：{str(e)[:100]}",
                     latency_ms=int((time.time() - t0) * 1000))
            await emit("thought", {"kind": "action", "step": stage,
                                   "text": f"{name} 通道异常（{str(e)[:60]}），尝试下一通道"})
    raise RuntimeError(f"所有引擎均不可用：{last_err}")


# ---------------------------------------------------------------- 主流程

async def run(question: str, emit, *, mode: str = prompts.DEFAULT_MODE) -> dict:
    """跑一次完整研判。emit(event, data) 负责推 SSE。"""
    run_id = uuid.uuid4().hex[:12]
    rec = Recorder(run_id)
    cfg = prompts.mode_config(mode)
    today = _today_cn()
    t_start = time.time()

    async def say(event, data):
        rec.event(event, data)
        await emit(event, data)

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
    await thought("action", "启动行情接口与公开网页检索，两路并行。", step="collect")

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
    hits, gap_search = await search_mod.search_web(
        queries, per_query=max(6, cfg["evidence"] // 2), topic=question,
        collected_by="sentiment", rerank_for=question, want=cfg["evidence"])
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
        rec.span("sentiment", "collect", "网页取证",
                 decision=f"候选 {len(hits)}，核验通过 {ok}",
                 evidence_ids=[e.ev_id for e in verified if e.fetch_status == "sourced"])

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
    model_name = result.get("model", "")
    engine_used = result.get("engine", PRIMARY)

    quality_before: Quality | None = None
    rounds = 0
    audit_history: list[dict] = []

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
        all_gaps = gaps + meta_gaps

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
                await thought("reflect",
                              f"仍未达标，但已用满 {cfg['name']}档的 {cfg['rework']} 轮返工额度。"
                              f"未达标项会如实写进报告，不掩盖。", step="audit", expert="auditor")
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

    # ---------------- 7. 笃行 ----------------
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

    # 专家产出统计：让「编排真实发生」变成可数的数字
    expert_stats = {}
    for sp in rec.spans:
        if sp.agent_id:
            row = expert_stats.setdefault(sp.agent_id, {"calls": 0, "evidence": 0})
            row["calls"] += 1
            row["evidence"] += len(sp.evidence_ids)
    for e in extras["experts"]:
        k = e.get("key")
        if k:
            expert_stats.setdefault(k, {"calls": 0, "evidence": 0})["finding"] = \
                str(e.get("finding", ""))[:200]

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
        "audit_history": audit_history,
        "metrics": m,
        "experts": [{"key": e["key"], "name": e["name"], "layer": e["layer"],
                     "role": e["role"], **expert_stats.get(e["key"], {})} for e in team],
        "trace": rec.to_dict(),
        "created_at": int(time.time()),
    }

    rid = store.save_report(payload)
    payload["id"] = rid
    if task_id:
        store.log_call(task_id=task_id, model=model_name or "deepseek-v4-pro",
                       question=question, engine=engine_used, report_id=rid,
                       share_url=payload["share_url"], elapsed_ms=elapsed_ms,
                       mode=cfg["key"])
    rec.persist()

    await say("result", {"id": rid, "taskId": task_id, "engine": engine_used,
                         "model": model_name, "share_url": payload["share_url"],
                         "elapsed_ms": elapsed_ms})
    await say("report", {k: payload[k] for k in
                         ("id", "question", "markdown", "verdict", "stance", "confidence",
                          "evidence", "claims", "tensions", "gaps", "redteam", "minority",
                          "actions", "triggers", "graph", "quality", "metrics",
                          "experts", "dimensions", "audit_history", "mode_config",
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
                if res.get("taskId"):
                    store.log_call(task_id=res["taskId"], model=res.get("model", ""),
                                   question=f"[深化] {section}", engine=name,
                                   report_id=report.get("id", ""),
                                   elapsed_ms=res.get("elapsed_ms", 0), mode="deepen")
                return md
        except Exception as e:
            last = e
    raise RuntimeError(f"深化失败：{last}")
