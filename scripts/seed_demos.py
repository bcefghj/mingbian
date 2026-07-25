# -*- coding: utf-8 -*-
"""跑真实研判，把结果冻成首页的示例卡片。

为什么要冻：一次深研档要几分钟。评委点开首页如果先看到几分钟转圈，
再好的东西也没人等。示例卡是真跑出来的真报告，只是提前跑好了。

用法：
    ./.venv/bin/python scripts/seed_demos.py            # 跑全部
    ./.venv/bin/python scripts/seed_demos.py scam gold  # 只跑指定几个
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.envload import load_env  # noqa: E402

load_env()

from app import demos, pipeline  # noqa: E402

CASES = [
    {"id": "scam", "tag": "反诈", "mode": "deep",
     "question": "有人推荐一个日返1.5%、稳赚不赔的理财项目，是骗局吗？"},
    {"id": "jobdd", "tag": "公司尽调", "mode": "deep",
     "question": "拿到一家 C 轮 AI 创业公司的 offer，值不值得去？"},
    {"id": "house", "tag": "楼市", "mode": "deep",
     "question": "2026 年下半年，一线城市房价还会继续跌吗？"},
    {"id": "gold", "tag": "贵金属", "mode": "deep",
     "question": "现在这个价位还值不值得买黄金？"},
    {"id": "btc", "tag": "加密资产", "mode": "deep",
     "question": "比特币这一轮见底了吗？"},
    {"id": "ai", "tag": "科技产业", "mode": "deep",
     "question": "AI 是不是泡沫？现在入行还来得及吗？"},
]


async def run_one(case: dict) -> dict | None:
    async def emit(event, data):
        if event == "status" and not str(data.get("message", "")).startswith("引擎推理中"):
            print(f"  ({case['id']}) {data.get('step')} {data.get('message','')}", flush=True)
        elif event == "gate":
            print(f"  ({case['id']}) 质检 {data.get('verdict')} {data.get('headline','')}", flush=True)
        elif event == "debate_gate":
            print(f"  ({case['id']}) 门控 {data.get('state')} 分 {data.get('score')}/"
                  f"{data.get('threshold')} 命中 {data.get('hit_count')} 项", flush=True)
        elif event == "debate_round":
            print(f"  ({case['id']}) {data.get('headline','')}", flush=True)
        elif event == "bench":
            print(f"  ({case['id']}) 已记入 Benchmark 曲线 {data.get('version')}", flush=True)
        elif event == "error":
            print(f"  ({case['id']}) 错误 {data}", flush=True)

    t0 = time.time()
    # 用 run 的返回值而不是 report 事件：事件为了减小 SSE 体积裁掉了 trace 与 calls，
    # 拿它当示例存盘的话，这几个案例的「决策回放」页会永远是空的——
    # 而这六个案例恰恰是评委最先点开的。
    rep = await pipeline.run(case["question"], emit, mode=case["mode"])
    if not rep:
        return None

    q = rep.get("quality") or {}
    n_ev = len([e for e in rep.get("evidence", []) if e.get("fetch_status") == "sourced"])
    # 只加字段，绝不覆盖 evidence / experts 本体。
    # 曾经这里把两个列表写成了展示用的一句话，结果示例报告页整页打不开，
    # 专家册也数不到示例里的出场记录——摘要放进 *_label，本体留着。
    rep.update({
        "id": case["id"],
        "tag": case["tag"],
        "demo": True,
        "headline": rep.get("verdict") or "",
        "experts_label": f"{len(rep.get('experts') or [])} 位专家",
        "evidence_label": f"{n_ev} 条证据 · {q.get('independent_domains', 0)} 个独立来源",
        "seeded_at": int(time.time()),
        "seconds": round(time.time() - t0, 1),
    })
    return rep


async def seed_one(case: dict, sem: asyncio.Semaphore):
    async with sem:
        print(f"[开始] {case['id']} · {case['question']}", flush=True)
        try:
            rep = await run_one(case)
        except Exception as e:
            print(f"[失败] {case['id']} 跑挂了：{str(e)[:160]}", flush=True)
            return
        if not rep:
            print(f"[跳过] {case['id']} 没拿到报告（旧示例保留）", flush=True)
            return
        path = os.path.join(demos.DEMO_DIR, f"{case['id']}.json")
        # 偶发：引擎这一跑没按格式回，解析出 0 条论点。这种空壳绝不能覆盖旧示例——
        # 首页那张卡会变成「0/0 论点有据」，比没更新还难看。
        if not (rep.get("claims") or []):
            print(f"[跳过] {case['id']} 本次解析出 0 条论点，保留旧示例，稍后重跑", flush=True)
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        q = rep.get("quality") or {}
        g = (rep.get("debate") or {}).get("gate") or {}
        print(f"[写入] {case['id']}｜{rep['seconds']}s｜质检 {q.get('verdict')}｜"
              f"论点 {len(rep.get('claims') or [])}｜证据 {len(rep.get('evidence') or [])}｜"
              f"独立源 {q.get('independent_domains')}｜门控 {g.get('state', '—')}｜"
              f"轨迹 {len(rep.get('trajectory') or [])} 点", flush=True)


async def main():
    want = set(sys.argv[1:])
    cases = [c for c in CASES if not want or c["id"] in want]
    os.makedirs(demos.DEMO_DIR, exist_ok=True)
    # 默认串行。实测并发跑时平台事件流会把不同任务的消息混在一起，
    # 曾经因此把 A 问题的报告写进了 B 问题的示例里。客户端已按 taskId 认领，
    # 但示例数据是要拿给人看的，这里宁可慢也不冒串台的风险。
    limit = int(os.getenv("SEED_CONCURRENCY", "1"))
    sem = asyncio.Semaphore(limit)
    print(f"共 {len(cases)} 个示例，并发 {limit}", flush=True)
    await asyncio.gather(*[seed_one(c, sem) for c in cases])
    print("全部结束", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
