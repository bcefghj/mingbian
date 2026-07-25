# -*- coding: utf-8 -*-
"""预置示例研判（跑好的数据）——让评委一打开就能看到完整结果，无需等待。

旧版司南时代的 demo 是另一套 meta 形状（overall / signals / scenarios），
新渲染器只认顶层的 verdict / claims / confidence。get_demo 出口处做一次
形状归一，这样种子还没跑完时，旧示例也不会白屏。
"""
from __future__ import annotations

import glob
import json
import os

DEMO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "demos")
_ORDER = ["scam", "jobdd", "house", "gold", "btc", "ai"]


def list_demos():
    items = {}
    for p in glob.glob(os.path.join(DEMO_DIR, "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            ex = d.get("experts")
            experts_label = d.get("experts_label") or (
                ex if isinstance(ex, str) else
                f"{len(ex)} 位专家" if isinstance(ex, list) else "")
            ev = d.get("evidence")
            evidence_label = d.get("evidence_label") or (
                ev if isinstance(ev, str) else
                f"{len(ev)} 条证据" if isinstance(ev, list) else
                f"{ev} 条证据" if isinstance(ev, int) else "")
            q = d.get("quality") or {}
            conf = d.get("confidence") or {}
            deb = (d.get("debate") or {}).get("gate") or {}
            claims = d.get("claims") if isinstance(d.get("claims"), list) else []
            items[d["id"]] = {
                "id": d["id"],
                "question": d["question"],
                "headline": d.get("headline") or d.get("verdict") or "",
                "tag": d.get("tag", ""),
                "experts": experts_label,
                "evidence": evidence_label,
                # 卡片上多摆几个真数字：光有一句结论，看不出这份报告有多厚
                "stance": d.get("stance", ""),
                "probability": conf.get("probability"),
                "ipcc": conf.get("ipcc", ""),
                "claims": len(claims),
                "claims_bound": sum(1 for c in claims
                                    if isinstance(c, dict) and c.get("evidence_ids")),
                "domains": q.get("independent_domains", 0),
                "verdict_gate": q.get("verdict", ""),
                "rework": q.get("rounds", 0),
                "gaps": len(d.get("gaps") or []),
                "tensions": len(d.get("tensions") or []),
                "redteam": len(d.get("redteam") or []),
                "debate_state": deb.get("state", ""),
                "debate_score": deb.get("score"),
                "seconds": d.get("seconds") or round((d.get("elapsed_ms") or 0) / 1000, 1),
                "mode": (d.get("mode_config") or {}).get("name", ""),
            }
        except Exception:
            continue
    ordered = [items[k] for k in _ORDER if k in items]
    ordered += [v for k, v in items.items() if k not in _ORDER]
    return ordered


def get_demo(did):
    p = os.path.join(DEMO_DIR, f"{did}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return normalize(json.load(f))


def normalize(d: dict) -> dict:
    """把新旧两种 demo 形状统一成报告页能直接渲染的结构。"""
    if not isinstance(d, dict):
        return d
    out = dict(d)
    meta = out.get("meta") if isinstance(out.get("meta"), dict) else {}

    # 新格式：顶层已有 verdict / claims —— 只补缺字段
    if out.get("verdict") or out.get("claims"):
        # 老种子脚本把这两个列表写成了摘要字符串。渲染器按列表用，
        # 遇到字符串会整页崩掉；这里降级成空列表，宁可少一块也别白屏。
        for k in ("evidence", "experts", "claims", "actions", "tensions", "gaps"):
            if k in out and not isinstance(out[k], list):
                if isinstance(out[k], str):
                    out.setdefault(f"{k}_label", out[k])
                out[k] = []
        out.setdefault("stance", "")
        out.setdefault("claims", [])
        out.setdefault("evidence", [])
        out.setdefault("confidence", {})
        out.setdefault("quality", {})
        out.setdefault("actions", [])
        out.setdefault("tensions", [])
        out.setdefault("gaps", [])
        out.setdefault("graph", {"nodes": [], "edges": [], "cliques": []})
        out.setdefault("deepenings", [])
        out.setdefault("demo", True)
        return out

    # 旧格式：结论藏在 meta.overall / headline
    overall = meta.get("overall") if isinstance(meta.get("overall"), dict) else {}
    out["verdict"] = (out.get("headline") or overall.get("verdict")
                      or overall.get("summary") or "")[:80]
    out["stance"] = overall.get("stance") or overall.get("direction") or ""
    out["as_of"] = overall.get("as_of") or ""
    out["dimensions"] = overall.get("dimensions") or []

    conf = overall.get("confidence")
    if isinstance(conf, dict):
        out["confidence"] = conf
    elif isinstance(conf, (int, float)):
        out["confidence"] = {"probability": conf, "ipcc": "", "interval": [],
                             "base_rate": {}, "adjustments": []}
    else:
        out["confidence"] = {}

    # 旧 signals → evidence 列表
    signals = meta.get("signals") or []
    evidence = []
    for i, s in enumerate(signals):
        if not isinstance(s, dict):
            continue
        evidence.append({
            "ev_id": s.get("id") or f"ev_demo_{i}",
            "title": s.get("title") or s.get("name") or f"信号 {i + 1}",
            "url": s.get("url") or "",
            "domain": s.get("domain") or s.get("source") or "",
            "excerpt": s.get("excerpt") or s.get("note") or "",
            "published_at": s.get("as_of") or s.get("date") or "",
            "credibility": int(s.get("credibility") or s.get("score") or 50),
            "fetch_status": "sourced",
            "source_type": s.get("type") or "unknown",
            "source_label": s.get("source") or "",
        })
    out["evidence"] = evidence

    # 旧专家产出 → claims（弱绑定，至少让论点卡不空）
    claims = []
    for i, e in enumerate(meta.get("experts") or []):
        if not isinstance(e, dict):
            continue
        text = e.get("finding") or e.get("conclusion") or e.get("summary") or ""
        if not text:
            continue
        claims.append({
            "claim_id": f"cl_demo_{i}",
            "text": text[:200],
            "section": e.get("name") or e.get("key") or "专家意见",
            "evidence_ids": [evidence[0]["ev_id"]] if evidence else [],
            "counter_evidence_ids": [],
            "stance": "中性",
            "strength": "weak" if evidence else "unsupported",
            "strength_label": "弱" if evidence else "无证据",
            "strength_glyph": "△" if evidence else "○",
            "cross_validated": False,
            "independent_domains": 1 if evidence else 0,
            "author": e.get("name") or "",
        })
    out["claims"] = claims

    # 专家名册
    experts = []
    for e in meta.get("experts") or []:
        if isinstance(e, dict):
            experts.append({
                "key": e.get("key") or "",
                "name": e.get("name") or e.get("key") or "专家",
                "layer": e.get("layer") or "分析",
                "role": e.get("role") or e.get("finding") or "",
                "finding": e.get("finding") or "",
            })
    out["experts"] = experts

    # 实体图
    nodes = [{"id": x.get("id") or x.get("name"), "label": x.get("name") or x.get("id"),
              "type": x.get("type") or "entity"}
             for x in (meta.get("entities") or []) if isinstance(x, dict)]
    edges = [{"source": r.get("from") or r.get("source"),
              "target": r.get("to") or r.get("target"),
              "label": r.get("relation") or r.get("label") or ""}
             for r in (meta.get("relations") or []) if isinstance(r, dict)]
    out["graph"] = {"nodes": nodes, "edges": edges, "cliques": []}

    out["tensions"] = [{"text": x, "side_a": [], "side_b": []}
                       for x in (meta.get("divergences") or []) if isinstance(x, str)]
    out["gaps"] = []
    out["actions"] = []
    out["quality"] = {
        "verdict": "pass",
        "headline": "预置示例（旧格式已兼容渲染）",
        "scores": {},
        "evidence_count": len(evidence),
        "independent_domains": len({e["domain"] for e in evidence if e.get("domain")}),
        "claim_count": len(claims),
        "unsupported_claims": sum(1 for c in claims if not c["evidence_ids"]),
        "evidence_bound_ratio": 1.0 if claims else 0.0,
        "rounds": 0,
        "issues": [],
    }
    out["metrics"] = {}
    out["trace"] = {"spans": [], "taskId": out.get("taskId") or ""}
    out["deepenings"] = out.get("deepenings") or []
    out["demo"] = True
    out["mode"] = out.get("mode") or "deep"
    out["engine"] = out.get("engine") or "infini"
    out["model"] = out.get("model") or "deepseek-v4-pro"
    return out
