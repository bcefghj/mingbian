# -*- coding: utf-8 -*-
"""跑 Benchmark 题集，把结果写进迭代曲线。

pipeline 末尾会自动认领题号并记一行，所以这里只负责按顺序把题跑完。

用法：
    ./.venv/bin/python scripts/run_bench.py              # 跑还没跑过的题
    ./.venv/bin/python scripts/run_bench.py --all        # 全部重跑
    ./.venv/bin/python scripts/run_bench.py reno health  # 只跑指定几道
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.envload import load_env  # noqa: E402

load_env()

from app import bench, pipeline, prompts  # noqa: E402

MODE = os.getenv("BENCH_MODE", "deep")


async def run_case(case: dict):
    cid = case["id"]
    print(f"[开始] {cid} · {case['q']}", flush=True)
    t0 = time.time()

    async def emit(event, data):
        if event == "status" and not str(data.get("message", "")).startswith("引擎推理中"):
            print(f"  ({cid}) {data.get('step')} {data.get('message', '')}", flush=True)
        elif event == "gate":
            print(f"  ({cid}) 质检 {data.get('verdict')} {data.get('headline', '')}", flush=True)
        elif event == "debate_gate":
            print(f"  ({cid}) 门控 {data.get('state')} 分 {data.get('score')} "
                  f"命中 {data.get('hit_count')} 项", flush=True)
        elif event == "debate_round":
            print(f"  ({cid}) {data.get('headline', '')}", flush=True)
        elif event == "bench":
            r = data.get("row") or {}
            print(f"  ({cid}) 已记入曲线 {data.get('version')}："
                  f"证据 {r.get('evidence_count')}、独立源 {r.get('independent_domains')}、"
                  f"返工 {r.get('rework_rounds')} 轮", flush=True)
        elif event == "error":
            print(f"  ({cid}) 错误 {data}", flush=True)

    try:
        await pipeline.run(case["q"], emit, mode=MODE)
    except Exception as e:
        print(f"[失败] {cid}：{str(e)[:160]}", flush=True)
        return
    print(f"[完成] {cid}｜{time.time() - t0:.0f}s", flush=True)


async def main():
    want = {a for a in sys.argv[1:] if not a.startswith("--")}
    snap = bench.snapshot()
    done = {cid for cid, r in snap["latest"].items()
            if r.get("version") == prompts.VERSION}

    if want:
        cases = [c for c in bench.CASES if c["id"] in want]
    elif "--all" in sys.argv:
        cases = list(bench.CASES)
    else:
        cases = [c for c in bench.CASES if c["id"] not in done]

    print(f"版本 {prompts.VERSION}｜档位 {MODE}｜待跑 {len(cases)} 道："
          f"{'、'.join(c['id'] for c in cases) or '无'}", flush=True)
    for c in cases:
        await run_case(c)

    snap = bench.snapshot()
    for c in snap["curve"]:
        print(f"{c['version']}：{c['runs']} 次运行 / 覆盖 {len(c['cases'])} 道题 · "
              f"独立源 {c['avg_domains']} · 绑证据 {c['bound_ratio']} · "
              f"一次过检 {c['first_pass_rate']} · 辩论开启 {c['debate_open_rate']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
