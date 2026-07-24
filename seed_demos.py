# -*- coding: utf-8 -*-
"""在服务器上用真实 InfiniSynapse API 跑一遍经典问题，生成「跑好的数据」+ 真实 taskId，
覆盖 data/demos/。评委打开首页即见真实调用产出的报告。
用法：./.venv/bin/python seed_demos.py            # 全部
      ./.venv/bin/python seed_demos.py scam house  # 指定
"""
import os
import sys
import json
import asyncio

sys.path.insert(0, os.path.dirname(__file__))
from app.envload import load_env
load_env()
from app import orchestrator

DEMO_DIR = os.path.join(os.path.dirname(__file__), "data", "demos")

QUESTIONS = [
    ("scam", "打假反诈 · 实时研判", "这个号称「稳赚不赔、日返3%」的项目是不是骗局？"),
    ("jobdd", "职场尽调 · 实时研判", "某公司给了offer，值不值得入职？"),
    ("house", "楼市 · 实时研判", "中国房价还会跌多久？"),
    ("gold", "黄金 · 实时研判", "现在适合买黄金吗？"),
    ("btc", "加密 · 实时研判", "比特币见底了吗？"),
    ("ai", "科技 · 实时研判", "AI 是不是泡沫？"),
]


async def run_one(did, tag, question):
    async def emit(ev, data):
        if ev in ("status", "plan"):
            print(f"    [{did}] {ev}: {str(data)[:70]}")
    print(f"==> 生成：{question}")
    try:
        r = await orchestrator.run(question, emit)
    except Exception as e:
        print(f"    ✗ 失败：{e}（保留原示例）")
        return
    md = r.get("markdown", "")
    if not md.strip():
        print("    ✗ 空报告，跳过")
        return
    meta = r.get("meta") or {}
    headline = (meta.get("overall") or {}).get("verdict", "")[:60]
    obj = {"id": did, "tag": tag, "question": question, "headline": headline,
           "experts": len(meta.get("experts", [])), "evidence": len(meta.get("signals", [])),
           "markdown": md, "meta": meta, "taskId": r.get("taskId"),
           "share_url": r.get("share_url", ""), "engine": r.get("engine", "infini"), "demo": True}
    json.dump(obj, open(os.path.join(DEMO_DIR, f"{did}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"    ✔ 保存，taskId={r.get('taskId')} 引擎={r.get('engine')}")


async def main():
    want = set(sys.argv[1:])
    for did, tag, q in QUESTIONS:
        if want and did not in want:
            continue
        await run_one(did, tag, q)
        await asyncio.sleep(2)
    print("完成。刷新 http://47.119.112.225/sinan/ 查看真实报告。")


if __name__ == "__main__":
    asyncio.run(main())
