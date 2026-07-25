# -*- coding: utf-8 -*-
"""实体归一与关联发现。

实体中心工作区的后端：点一个实体，要能立刻拿出它的全部证据、
关联对象与时间线。另外做一件事——发现「一致性团伙」：
多个来源口径高度雷同，往往不是英雄所见略同，而是同一稿子的复制。
"""
from __future__ import annotations

import re
from collections import defaultdict

from .models import Evidence

# 归一化时要去掉的后缀
_SUFFIX = ("股份有限公司", "有限责任公司", "有限公司", "集团", "公司", "科技", "控股")
_PUNCT = re.compile(r"[\s\u3000·・()（）\[\]【】\"'“”‘’,，。;；]")


def normalize(name: str) -> str:
    s = _PUNCT.sub("", (name or "")).lower()
    for suf in _SUFFIX:
        if s.endswith(suf) and len(s) > len(suf) + 1:
            s = s[: -len(suf)]
    return s


def build(raw_entities: list[dict], relations: list[dict],
          evidence: dict[str, Evidence], claims: list[dict]) -> dict:
    """把模型给的实体表 + 证据 + 论点，织成一张可点击的图。"""
    nodes: dict[str, dict] = {}

    def touch(name: str, etype: str = "其他", note: str = "") -> dict | None:
        name = (name or "").strip()
        if not name or len(name) > 40:
            return None
        key = normalize(name)
        if not key:
            return None
        node = nodes.get(key)
        if not node:
            node = {"key": key, "name": name, "type": etype, "note": note,
                    "aliases": set(), "evidence_ids": set(), "claim_ids": set(),
                    "mentions": 0}
            nodes[key] = node
        node["mentions"] += 1
        if name != node["name"]:
            node["aliases"].add(name)
        if note and not node["note"]:
            node["note"] = note
        if etype and etype != "其他" and node["type"] == "其他":
            node["type"] = etype
        return node

    for e in raw_entities or []:
        if not isinstance(e, dict):
            continue
        node = touch(str(e.get("name", "")), str(e.get("type", "其他")),
                     str(e.get("note", "")))
        if not node:
            continue
        for ref in e.get("evidence") or []:
            if str(ref) in evidence:
                node["evidence_ids"].add(str(ref))

    # 证据正文里再扫一遍：实体在哪些证据里被提到
    for ev in evidence.values():
        blob = f"{ev.title} {ev.excerpt}"
        for node in nodes.values():
            if node["name"] and node["name"] in blob:
                node["evidence_ids"].add(ev.ev_id)

    # 论点里提到的实体
    for c in claims or []:
        text = c.get("text", "")
        for node in nodes.values():
            if node["name"] and node["name"] in text:
                node["claim_ids"].add(c.get("claim_id", ""))

    edges = []
    seen_edge = set()
    for r in relations or []:
        if not isinstance(r, dict):
            continue
        a, b = touch(str(r.get("from", ""))), touch(str(r.get("to", "")))
        if not a or not b or a["key"] == b["key"]:
            continue
        sig = (a["key"], b["key"], str(r.get("label", "")))
        if sig in seen_edge:
            continue
        seen_edge.add(sig)
        edges.append({"from": a["key"], "to": b["key"],
                      "label": str(r.get("label", ""))[:24], "kind": "declared"})

    # 共现边：同一条证据里同时出现的两个实体
    by_ev: dict[str, list[str]] = defaultdict(list)
    for node in nodes.values():
        for eid in node["evidence_ids"]:
            by_ev[eid].append(node["key"])
    for eid, keys in by_ev.items():
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                sig = tuple(sorted((keys[i], keys[j]))) + ("共同出现",)
                if sig in seen_edge:
                    continue
                seen_edge.add(sig)
                edges.append({"from": sig[0], "to": sig[1], "label": "共同出现",
                              "kind": "cooccur", "evidence_id": eid})

    out_nodes = []
    for n in nodes.values():
        out_nodes.append({
            "key": n["key"], "name": n["name"], "type": n["type"], "note": n["note"],
            "aliases": sorted(n["aliases"]), "evidence_ids": sorted(n["evidence_ids"]),
            "claim_ids": sorted(x for x in n["claim_ids"] if x),
            "mentions": n["mentions"],
            "domains": sorted({evidence[e].domain for e in n["evidence_ids"]
                               if e in evidence and evidence[e].domain}),
        })
    out_nodes.sort(key=lambda x: (-len(x["evidence_ids"]), -x["mentions"]))
    return {"nodes": out_nodes, "edges": edges,
            "cliques": find_cliques(evidence)}


_STOP = set("的了是在和与及對对为于就都而或也很非常这那一个我们他们你们它们可以已经因为所以但是如果"
            "什么怎么这样那样通过进行相关表示认为目前当前今年去年市场投资分析报告数据")


def _shingles(text: str, n: int = 4) -> set[str]:
    t = re.sub(r"[^\u4e00-\u9fffa-zA-Z0-9]", "", text or "")
    for w in _STOP:
        t = t.replace(w, "")
    return {t[i:i + n] for i in range(0, max(0, len(t) - n + 1))}


def find_cliques(evidence: dict[str, Evidence], threshold: float = 0.42) -> list[dict]:
    """一致性团伙：不同域名但正文高度雷同，多半是同一稿源的批量转载。

    这在反诈与舆情场景里特别有用——「十家媒体都这么说」经常是
    「一家在说，九家在转」。
    """
    items = [(e.ev_id, e.domain, _shingles(e.excerpt))
             for e in evidence.values() if len(e.excerpt or "") >= 60]
    groups: list[dict] = []
    used: set[str] = set()
    for i in range(len(items)):
        aid, adom, ash = items[i]
        if aid in used or not ash:
            continue
        members = [{"ev_id": aid, "domain": adom}]
        for j in range(i + 1, len(items)):
            bid, bdom, bsh = items[j]
            if bid in used or not bsh:
                continue
            inter = len(ash & bsh)
            union = len(ash | bsh) or 1
            if inter / union >= threshold:
                members.append({"ev_id": bid, "domain": bdom})
                used.add(bid)
        if len(members) >= 2:
            used.add(aid)
            doms = sorted({m["domain"] for m in members if m["domain"]})
            groups.append({
                "members": members, "domains": doms, "size": len(members),
                "note": f"{len(members)} 条证据文本高度雷同，涉及 {len(doms)} 个域名——"
                        f"疑似同一稿源转载，独立性应打折",
            })
    return groups
