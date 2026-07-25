# -*- coding: utf-8 -*-
"""全链路 Trace 与事件日志。

每个 run 一个 Recorder：收集 TraceSpan、事件流、告警。
事件带自增 event_id，前端断线后可用 Last-Event-ID 续传。
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter

from .models import TraceSpan

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports", "_runs")
os.makedirs(LOG_DIR, exist_ok=True)

# 流式事件每来一个 token 就重发一次「到目前为止的全文」。
# 原样记下来，一次运行的事件日志能到一百多兆——因为同一份报告被存了几千遍。
# 这类事件只留长度，正文在报告里本来就有一份完整的。
_VOLATILE = {"text", "engine_probe"}
_MAX_EVENTS = 4000


def _slim(etype: str, data: dict) -> dict:
    if etype not in _VOLATILE or not isinstance(data, dict):
        return data
    out = {}
    for k, v in data.items():
        if isinstance(v, str) and len(v) > 120:
            out[k] = v[:120] + "…"
            out[f"{k}_len"] = len(v)
        else:
            out[k] = v
    return out

# 观测告警规则（学 12 狼人杀 observability/DESIGN.md 的阈值表）
ALERT_RULES = {
    "provider_429_burst": 3,
    "structured_invoke_gave_up": 10,
    "agent_fallback_per_run": 5,
    "error_events_per_run": 3,
    "retrieval_failed_per_run": 6,
}


class Recorder:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.spans: list[TraceSpan] = []
        self.events: list[dict] = []
        self.counters: Counter = Counter()
        self.alerts: list[dict] = []
        # 一次研判会打好几次引擎（成文、质检、返工、辩论各算一次），
        # 每次都有自己的 taskId。只记最后一次，台账就漏掉了大半条链路。
        self.calls: list[dict] = []
        # 主引擎顶不住时退到备用通道。这件事要一路带到报告里，
        # 让看报告的人知道这份结论是在什么条件下产出的。
        self.degraded: dict | None = None
        self._seq = 0
        self._eid = 0
        self.started = time.time()

    # ---- Trace ----

    def span(self, agent_id: str, stage: str, purpose: str, *, model: str = "",
             decision: str = "", evidence_ids: list[str] | None = None,
             prompt_chars: int = 0, output_chars: int = 0,
             latency_ms: int = 0) -> TraceSpan:
        self._seq += 1
        sp = TraceSpan(
            seq=self._seq, agent_id=agent_id, stage=stage, purpose=purpose,
            model=model, decision=decision, evidence_ids=evidence_ids or [],
            prompt_chars=prompt_chars, output_chars=output_chars,
            latency_ms=latency_ms,
        )
        self.spans.append(sp)
        return sp

    # ---- 事件（同时喂 SSE 与落盘）----

    def event(self, etype: str, data: dict) -> dict:
        self._eid += 1
        row = {"event_id": self._eid, "type": etype, "ts": time.time(),
               "data": _slim(etype, data)}
        self.events.append(row)
        return row

    # ---- 计数与告警 ----

    def bump(self, key: str, n: int = 1):
        self.counters[key] += n
        limit = ALERT_RULES.get(key)
        if limit and self.counters[key] > limit:
            already = any(a["code"] == key for a in self.alerts)
            if not already:
                self.alerts.append({
                    "code": key, "level": "warning", "run_id": self.run_id,
                    "count": self.counters[key], "limit": limit, "ts": time.time(),
                })

    @property
    def elapsed_ms(self) -> int:
        return int((time.time() - self.started) * 1000)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "elapsed_ms": self.elapsed_ms,
            "spans": [s.to_dict() for s in self.spans],
            "counters": dict(self.counters),
            "alerts": self.alerts,
        }

    def persist(self):
        """落盘：run 元信息 + 事件日志 + 告警。失败不影响主流程。"""
        try:
            base = os.path.join(LOG_DIR, self.run_id)
            os.makedirs(base, exist_ok=True)
            with open(os.path.join(base, "trace.json"), "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            # 再加一道保险：只留首尾各一半，中间是重复度最高的流式片段
            rows = self.events
            if len(rows) > _MAX_EVENTS:
                half = _MAX_EVENTS // 2
                rows = rows[:half] + [{"event_id": -1, "type": "truncated", "ts": time.time(),
                                       "data": {"dropped": len(rows) - _MAX_EVENTS}}] + rows[-half:]
            with open(os.path.join(base, "events.jsonl"), "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if self.alerts:
                with open(os.path.join(LOG_DIR, "alerts.jsonl"), "a", encoding="utf-8") as f:
                    for a in self.alerts:
                        f.write(json.dumps(a, ensure_ascii=False) + "\n")
        except Exception:
            pass
