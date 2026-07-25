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
        try:
            await emit("status", {"step": "plan", "message": "唤醒研判引擎…"})
            result = await eng.run_analysis(question, task_text, emit)
            if result and result.get("markdown", "").strip():
                return result
            last_err = RuntimeError("引擎返回空报告")
            await emit("status", {"step": "plan", "message": "通道波动，正在切换备用通道…"})
        except Exception as e:
            last_err = e
            msg = str(e)[:80]
            # 用户可见文案不暴露具体供应商名称
            await emit("status", {"step": "plan", "message": "主通道暂不可用，切换备用通道…"})
            await emit("thought", {"kind": "reflect", "text": f"取证通道受阻（{msg}），改走备用路径继续研判…"})
            continue
    raise RuntimeError(f"研判失败：{last_err}")
