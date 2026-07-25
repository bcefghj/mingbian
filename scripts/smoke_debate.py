# -*- coding: utf-8 -*-
"""不烧额度的流水线冒烟测试。

把引擎调用与联网取证换成固定桩，只验编排本身：门控算得对不对、
辩论轮次走不走得通、立场轨迹每个点有没有落下、payload 结构齐不齐。

用法：./.venv/bin/python scripts/smoke_debate.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.envload import load_env  # noqa: E402

load_env()

from app import pipeline  # noqa: E402
from app.collectors import market, search as search_mod, web as web_mod  # noqa: E402
from app.models import Evidence  # noqa: E402

REPORT = """# 研判：测试问题

## 核心结论
这是一份用于冒烟测试的报告 [E1]。

## 关键证据
- 证据一 [E1]
- 证据二 [E2]

## 反方观点（红队）
红队认为样本不足。

## 行动建议
先小仓位试。

```mb-meta
{"verdict":"测试结论：方向偏空但把握有限","stance":"看空","as_of":"2026-07",
 "dimensions":["维度一","维度二","维度三"],
 "base_rate":{"value":0.5,"basis":"测试基准","source":"test"},
 "adjustments":[{"delta":-0.06,"reason":"反向信号"}],
 "evidence":[{"ref":"E1","title":"标题一","url":"https://stats.gov.cn/a",
              "source_type":"statistics","published_at":"2026-06-01","excerpt":"摘录一"},
             {"ref":"E2","title":"标题二","url":"https://example.org/b",
              "source_type":"research","published_at":"2026-05-01","excerpt":"摘录二"}],
 "claims":[{"text":"论点甲","section":"维度一","evidence":["E1","E2"],"stance":"支持"},
           {"text":"论点乙","section":"维度二","evidence":["E1"],"counter_evidence":["E2"],"stance":"反对"},
           {"text":"论点丙无据","section":"维度三","evidence":[]}],
 "tensions":[{"topic":"分歧点","side_a":{"stance":"甲方","quote":"q1","evidence":["E1"]},
              "side_b":{"stance":"乙方","quote":"q2","evidence":["E2"]},"summary":"谈不拢"}],
 "redteam":["反驳一","反驳二","反驳三"],
 "minority":["少数派意见"],
 "gaps":[{"topic":"缺口一","queries_tried":["词1","词2"],"note":"没搜到"}],
 "actions":[{"text":"动作一","kind":"do"}],
 "triggers":["信号一"],
 "entities":[{"name":"实体甲","type":"机构","note":"角色","evidence":["E1"]}],
 "relations":[{"from":"实体甲","to":"实体乙","label":"关联"}],
 "experts":[{"key":"chief","finding":"一句话结论"}]}
```"""

ATTACK = json.dumps({
    "points": [{"claim": "论点甲", "attack": "样本区间选择性偏差",
                "falsifiable": "换用全区间数据复算即可判定", "severity": "high"}],
    "strongest": "样本区间是挑出来的",
}, ensure_ascii=False)

JUDGE = json.dumps({
    "rulings": [{"attack": "样本区间", "verdict": "partial", "reason": "部分成立"}],
    "stance_after": "看空",
    "probability_delta": -0.07,
    "concessions": ["应标注样本区间"],
    "residual_disagreement": ["全区间数据尚未取得"],
    "summary": "攻击部分成立，把握程度下调。",
}, ensure_ascii=False)


async def fake_engine(prompt_text, emit, rec, *, purpose, agent, stage, timeout=None):
    body = {"debate_attack": ATTACK, "debate_judge": JUDGE,
            "audit": json.dumps({"scores": {"evidence_sufficiency": 80,
                                            "dimension_completeness": 80,
                                            "conclusion_confidence": 80,
                                            "structure_integrity": 80,
                                            "cross_validation": 80},
                                 "verdict": "pass", "issues": [],
                                 "review": "桩评审"}, ensure_ascii=False)}.get(purpose, REPORT)
    rec.span(agent, stage, purpose, model="stub", decision="桩返回")
    meta = None
    if purpose not in ("debate_attack", "debate_judge", "audit"):
        meta = json.loads(body.split("```mb-meta")[1].split("```")[0])
    return {"markdown": body, "meta": meta, "taskId": "stub-task-0001",
            "engine": "infini", "model": "deepseek-v4-pro", "share_url": ""}


async def fake_market(question, emit=None):
    return [], []


async def fake_search(queries, **kw):
    out = []
    for i, host in enumerate(["stats.gov.cn", "example.org", "pbc.gov.cn",
                              "reuters.com", "caixin.com", "sse.com.cn"]):
        out.append({"url": f"https://{host}/p{i}", "title": f"来源{i}",
                    "snippet": "摘要", "channel": "stub"})
    return out, None


async def fake_verify_many(hits, limit=10):
    evs = []
    for h in hits[:limit]:
        e = Evidence(url=h["url"], title=h["title"], excerpt="正文摘录",
                     source_type="research", published_at="2026-06-01")
        e.fetch_status = "sourced"
        evs.append(e)
    return evs


async def fake_verify_evidence(evs, limit=16):
    for e in evs:
        e.fetch_status = "sourced"
    return len(evs)


async def main():
    pipeline._call_engine = fake_engine
    market.collect_market = fake_market
    search_mod.search_web = fake_search
    web_mod.verify_many = fake_verify_many
    web_mod.verify_evidence = fake_verify_evidence

    events = []

    async def emit(ev, data):
        events.append((ev, data))

    payload = await pipeline.run("比特币这一轮见底了吗？测试用问题", emit, mode="deep")

    kinds = [e for e, _ in events]
    print("事件类型：", sorted(set(kinds)))
    g = payload["debate"]["gate"]
    print(f"\n门控 state={g['state']} score={g['score']} 阈值={g['threshold']} "
          f"命中={g['hit_count']}/{len(g['signals'])}")
    print("  理由：", g["reason"])
    for s in g["signals"]:
        print(f"   [{'✓' if s['hit'] else ' '}] {s['name']:<12} +{s['weight']}  {s['detail'][:52]}")

    print(f"\n辩论轮次 {len(payload['debate']['rounds'])}，结果 {payload['debate']['outcome']}")
    for rd in payload["debate"]["rounds"]:
        print("  ", rd["headline"])

    print("\n立场轨迹：")
    for p in payload["trajectory"]:
        prob = f"{p['probability'] * 100:.0f}%" if p["probability"] is not None else "—"
        print(f"  {p['seq']}. {p['stage_cn']:<10} {p['shift_kind']:<8} {prob:>5}  {p['shift'][:56]}")
    print("概括：", payload["trajectory_summary"]["headline"])
    print("修正：", payload.get("revision"))

    conf = payload["confidence"]
    print(f"\n置信度阶梯 {len(conf['adjustments'])} 项调整（含辩论 "
          f"{sum(1 for a in conf['adjustments'] if a.get('from') == 'debate')} 项），"
          f"最终 {conf['probability']}")

    missing = [k for k in ("debate", "trajectory", "trajectory_summary", "revision",
                           "quality", "metrics", "graph", "claims", "evidence")
               if k not in payload]
    print("\npayload 缺字段：", missing or "无")
    stance_events = [d for e, d in events if e == "stance"]
    print(f"SSE stance 事件 {len(stance_events)} 个，"
          f"debate_gate {kinds.count('debate_gate')} 个，"
          f"debate_round {kinds.count('debate_round')} 个")

    if "--keep" in sys.argv:
        print(f"\n已保留桩报告用于前端联调：/report/{payload['id']}"
              f"（记得跑一次 --clean 清掉，别让它进统计口径）")
        return
    _cleanup(payload["id"], payload["run_id"])
    print("桩数据已清理，未污染报告库与台账。")


def _cleanup(rid: str, run_id: str):
    """桩跑出来的报告会混进全站统计口径里，跑完就地删掉。"""
    import shutil
    from app import store

    p = os.path.join(store.REPORT_DIR, f"{rid}.json")
    if os.path.exists(p):
        os.remove(p)
    shutil.rmtree(os.path.join(store.REPORT_DIR, "_runs", run_id), ignore_errors=True)
    if os.path.exists(store.LEDGER_PATH):
        with open(store.LEDGER_PATH, encoding="utf-8") as f:
            rows = [l for l in f if "stub-task-0001" not in l]
        with open(store.LEDGER_PATH, "w", encoding="utf-8") as f:
            f.writelines(rows)


if __name__ == "__main__":
    asyncio.run(main())
