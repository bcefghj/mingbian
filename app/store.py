# -*- coding: utf-8 -*-
"""报告存储 + InfiniSynapse 调用台账。

台账是给评委核验用的：比赛前置准入条件要求「调用日志可在平台后台查验」，
所以每一次真实的 Infini 调用都要留下 taskId、模型名与时间。
"""
from __future__ import annotations

import glob
import json
import os
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(__file__))
REPORT_DIR = os.path.join(ROOT, "reports")
LEDGER_PATH = os.path.join(REPORT_DIR, "ledger.jsonl")
REVIEW_PATH = os.path.join(REPORT_DIR, "reviews.json")
WATCH_PATH = os.path.join(REPORT_DIR, "watchlist.json")
os.makedirs(REPORT_DIR, exist_ok=True)


# ---------------------------------------------------------------- 报告

def save_report(payload: dict, rid: str | None = None) -> str:
    rid = rid or payload.get("id") or uuid.uuid4().hex[:12]
    payload = dict(payload)
    payload["id"] = rid
    payload.setdefault("created_at", int(time.time()))
    with open(os.path.join(REPORT_DIR, f"{rid}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return rid


def get_report(rid: str) -> dict | None:
    if not rid or "/" in rid or ".." in rid:
        return None
    p = os.path.join(REPORT_DIR, f"{rid}.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def update_report(rid: str, **fields) -> dict | None:
    rep = get_report(rid)
    if not rep:
        return None
    rep.update(fields)
    save_report(rep, rid)
    return rep


def list_reports(limit: int = 50) -> list[dict]:
    out = []
    for p in glob.glob(os.path.join(REPORT_DIR, "*.json")):
        name = os.path.basename(p)
        if name in ("ledger.jsonl", "reviews.json", "watchlist.json"):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            out.append({
                "id": d.get("id"), "question": d.get("question", ""),
                "created_at": d.get("created_at", 0),
                "engine": d.get("engine", ""), "model": d.get("model", ""),
                "taskId": d.get("taskId"),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return out[:limit]


# ---------------------------------------------------------------- 调用台账

def log_call(*, task_id: str, model: str, question: str, engine: str = "infini",
             report_id: str = "", share_url: str = "", elapsed_ms: int = 0,
             mode: str = "", status: str = "ok", purpose: str = "", agent: str = "",
             prompt_chars: int = 0, output_chars: int = 0):
    """记录一次真实的引擎调用。

    记的是「编排实际发出了多少次推理请求」，所以每次成功返回都写一行。
    平台回写了回执号（taskId）的那些额外可按号复查，但没有回执号不等于
    这次调用没发生——把它们漏掉，台账的条数就对不上编排的实际动作。
    """
    row = {
        "ts": int(time.time()), "taskId": task_id, "model": model,
        "engine": engine, "question": question[:120], "report_id": report_id,
        "share_url": share_url, "elapsed_ms": elapsed_ms, "mode": mode,
        "status": status, "purpose": purpose, "agent": agent,
        "prompt_chars": prompt_chars, "output_chars": output_chars,
    }
    try:
        with open(LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return row


def read_ledger(limit: int = 200) -> list[dict]:
    if not os.path.exists(LEDGER_PATH):
        return []
    rows = []
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
    except Exception:
        return []
    rows.reverse()
    return rows[:limit]


def ledger_stats() -> dict:
    rows = read_ledger(2000)
    with_task = [r for r in rows if r.get("taskId")]
    total_ms = sum(r.get("elapsed_ms") or 0 for r in rows)
    by_purpose: dict[str, dict] = {}
    for r in rows:
        p = r.get("purpose") or "未标注环节"
        b = by_purpose.setdefault(p, {"purpose": p, "count": 0, "ms": 0, "out": 0})
        b["count"] += 1
        b["ms"] += r.get("elapsed_ms") or 0
        b["out"] += r.get("output_chars") or 0
    for b in by_purpose.values():
        b["avg_ms"] = int(b["ms"] / b["count"]) if b["count"] else 0
    runs = {r.get("report_id") for r in rows if r.get("report_id")}
    return {
        "total_calls": len(rows),
        "with_task_id": len(with_task),
        "avg_elapsed_ms": int(total_ms / len(rows)) if rows else 0,
        "total_elapsed_ms": total_ms,
        "total_output_chars": sum(r.get("output_chars") or 0 for r in rows),
        "models": sorted({r.get("model") for r in rows if r.get("model")}),
        "runs": len(runs),
        "calls_per_run": round(len(rows) / len(runs), 1) if runs else 0,
        "by_purpose": sorted(by_purpose.values(), key=lambda b: -b["count"]),
        "shared": sum(1 for r in rows if r.get("share_url")),
    }


# ---------------------------------------------------------------- 复核队列

def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _dump_json(path: str, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def set_review(report_id: str, claim_id: str, verdict: str, note: str = ""):
    """人工复核一条论点：已核实 / 存疑 / 驳回。"""
    data = _load_json(REVIEW_PATH, {})
    data.setdefault(report_id, {})[claim_id] = {
        "verdict": verdict, "note": note, "ts": int(time.time()),
    }
    _dump_json(REVIEW_PATH, data)
    return data[report_id][claim_id]


def get_reviews(report_id: str) -> dict:
    return _load_json(REVIEW_PATH, {}).get(report_id, {})


def review_stats() -> dict:
    data = _load_json(REVIEW_PATH, {})
    flat = [v for per in data.values() for v in per.values()]
    return {
        "reviewed": len(flat),
        "confirmed": sum(1 for v in flat if v.get("verdict") == "confirmed"),
        "doubted": sum(1 for v in flat if v.get("verdict") == "doubted"),
        "rejected": sum(1 for v in flat if v.get("verdict") == "rejected"),
    }


# ---------------------------------------------------------------- 关注清单

def add_watch(topic: str, report_id: str = "") -> dict:
    items = _load_json(WATCH_PATH, [])
    item = {"id": uuid.uuid4().hex[:8], "topic": topic,
            "report_id": report_id, "ts": int(time.time())}
    items.insert(0, item)
    _dump_json(WATCH_PATH, items[:100])
    return item


def list_watch() -> list[dict]:
    return _load_json(WATCH_PATH, [])
