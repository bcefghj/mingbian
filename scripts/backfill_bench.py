# -*- coding: utf-8 -*-
"""把已经落库的历史研判补记进 Benchmark 曲线。

Benchmark 的记录点是在 pipeline 末尾写的，而在这之前已经有一批真实运行
躺在 reports/ 和 data/demos/ 里。这些是真跑出来的数据，不补进去，
曲线上就只剩最新版本一个点，"证明这一版比上一版好"也就无从谈起。

版本判定不靠猜：报告里有没有 debate 字段，直接决定它属于哪一版——
辩论门控是 v1.1 才有的东西。

用法：
    ./.venv/bin/python scripts/backfill_bench.py          # 补记
    ./.venv/bin/python scripts/backfill_bench.py --reset  # 先清空再补
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.envload import load_env  # noqa: E402

load_env()

from app import bench, demos, store  # noqa: E402

# 有 debate 字段说明跑在辩论门控上线之后
V_OLD, V_NEW = "v1.0", "v1.1"

# 论点数为 0 的是中途失败的运行，不是一次有效研判，不进曲线
MIN_CLAIMS = 1


def load_all() -> list[dict]:
    docs = []
    for p in glob.glob(os.path.join(demos.DEMO_DIR, "*.json")):
        try:
            docs.append(json.load(open(p, encoding="utf-8")))
        except Exception:
            continue
    for p in glob.glob(os.path.join(store.REPORT_DIR, "*.json")):
        if os.path.basename(p) in ("reviews.json", "watchlist.json"):
            continue
        try:
            docs.append(json.load(open(p, encoding="utf-8")))
        except Exception:
            continue
    return docs


def main():
    if "--reset" in sys.argv and os.path.exists(bench.BENCH_PATH):
        os.remove(bench.BENCH_PATH)
        print("已清空 data/bench.jsonl")

    added = skipped = 0
    for d in sorted(load_all(), key=lambda x: x.get("created_at") or 0):
        cid = bench.match_case(d.get("question", ""))
        if not cid:
            skipped += 1
            continue
        if len(d.get("claims") or []) < MIN_CLAIMS:
            print(f"  跳过 {d.get('id')}（{cid}）：论点为 0，属于失败运行")
            skipped += 1
            continue
        ver = V_NEW if d.get("debate") else V_OLD
        row = bench.record(cid, ver, d)
        if row:
            added += 1
            print(f"  记入 {ver} · {cid:6} · 证据 {row['evidence_count']:3} · "
                  f"独立源 {row['independent_domains']:2} · 返工 {row['rework_rounds']} 轮 · "
                  f"{row['elapsed_ms'] / 1000:.0f}s")
        else:
            skipped += 1

    snap = bench.snapshot()
    print(f"\n补记 {added} 行，跳过 {skipped} 条。")
    for c in snap["curve"]:
        print(f"  {c['version']}：{c['runs']} 次运行，覆盖 {len(c['cases'])} 道题，"
              f"平均独立来源 {c['avg_domains']}，绑证据率 {c['bound_ratio']}，"
              f"一次过检 {c['first_pass_rate']}，均耗时 {c['avg_seconds']}s")
    missing = [c["id"] for c in bench.CASES if c["id"] not in snap["latest"]]
    print("尚未跑过的题：", "、".join(missing) or "无")


if __name__ == "__main__":
    main()
