# -*- coding: utf-8 -*-
"""引擎编排：默认优先 MiniMax（快、有额度）；InfiniSynapse 作次选（比赛主叙事，余额不足时秒切）。"""
import os
from . import infini, minimax
from .prompts import build_task_text

PRIMARY = os.getenv("PRIMARY_ENGINE", "minimax").lower()


async def run(question: str, emit):
    task_text = build_task_text(question)
    # 余额不足时 Infini 会长时间空转；默认 MiniMax 优先保证体验
    if PRIMARY == "infini":
        order = [infini, minimax]
    else:
        order = [minimax, infini]
    last_err = None
    for eng in order:
        name = "InfiniSynapse" if eng is infini else "MiniMax"
        try:
            await emit("status", {"step": "plan", "message": f"启用引擎：{name}"})
            result = await eng.run_analysis(question, task_text, emit)
            if result and result.get("markdown", "").strip():
                return result
            last_err = RuntimeError(f"{name} 返回空报告")
            await emit("status", {"step": "plan", "message": f"{name} 空报告，切换兜底…"})
        except Exception as e:
            last_err = e
            msg = str(e)[:100]
            await emit("status", {"step": "plan", "message": f"{name} 不可用（{msg}），切换兜底…"})
            await emit("thought", {"kind": "reflect", "text": f"{name} 失败：{msg}。正在切换引擎…"})
            continue
    raise RuntimeError(f"所有引擎均失败：{last_err}")
