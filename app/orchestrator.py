# -*- coding: utf-8 -*-
"""引擎编排：默认优先 InfiniSynapse 官方 API；卡住/失败自动切 MiniMax 兜底。"""
import os
from . import infini, minimax
from .prompts import build_task_text

PRIMARY = os.getenv("PRIMARY_ENGINE", "infini").lower()


async def run(question: str, emit):
    task_text = build_task_text(question)
    order = [infini, minimax] if PRIMARY == "infini" else [minimax, infini]
    last_err = None
    for eng in order:
        name = "InfiniSynapse" if eng is infini else "MiniMax"
        try:
            await emit("status", {"step": "plan", "message": f"启用引擎：{name}"})
            result = await eng.run_analysis(question, task_text, emit)
            if result and result.get("markdown", "").strip():
                return result
        except Exception as e:
            last_err = e
            await emit("status", {"step": "plan",
                                  "message": f"{name} 不可用（{str(e)[:80]}），尝试兜底引擎..."})
            continue
    raise RuntimeError(f"所有引擎均失败：{last_err}")
