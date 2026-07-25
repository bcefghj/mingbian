# 明辨 MINGBIAN · 交接文档（给下一任 AI）

> 最后更新：2026-07-26  
> 对话主 transcript：[`明辨重构`](77a01a64-d1af-4c15-bd9d-eab30ff2c96f)  
> 仓库：https://github.com/bcefghj/mingbian  
> 公网：http://47.119.112.225/mingbian/  
> 当前版本：`v1.1`（见 `app/prompts.py` 的 `VERSION`）

本文目标：**让你在不重读整段对话的情况下，能接着改、接着部署、接着答辩，而且不踩已经踩过的坑。**

---

## 0. 30 秒现状

| 项 | 状态 |
|---|---|
| 产品名 | **明辨 MINGBIAN**（前身：司南 SINAN；**不要**用「数字先知」这个名字） |
| 主引擎对外口径 | **永远**写 `InfiniSynapse · deepseek-v4-pro` |
| 主引擎不可用时 | 内部可静默换路重试；**UI / 报告 / 台账 / 回放不许出现备用引擎名** |
| 流水线 | 八节点 DAG：intake → plan → 博学 → 审问 → 慎思 → 质检 → **明辨(辩论)** → 笃行 |
| 本地 | `http://127.0.0.1:8767`（`./.venv/bin/python run.py`） |
| 服务器 | `47.119.112.225`，systemd `mingbian.service`，nginx `/mingbian/`，端口 `8767` |
| 同机邻居 | Anker 在 `8766` / `/anker/`——**绝不动** |
| 示例 | `data/demos/{scam,jobdd,house,gold,btc,ai}.json`，6 份真跑报告 |
| 台账 | `reports/ledger.jsonl`（gitignore）；服务器上已回填约 162 条 |
| GitHub | `origin` → `bcefghj/mingbian`；旧仓 `bcefghj/sinan` 留着作历史 |

---

## 1. 这个项目到底是什么

比赛：InfiniSynapse × CSDN「Vibe Coding」泛数据分析应用开发大赛。

产品一句话：用户问一个需要下判断的问题 → 派出专家团联网取证 → 交叉质询 / 红队 / 选择性辩论 → 给出**每条结论绑证据、每个概率可审计**的报告（包括没查到什么）。

硬合规（评委要查的）：

1. 后端走 InfiniSynapse Server API，模型显式锁 `deepseek-v4-pro`
2. 调用日志可在平台后台核验（`/ledger` 有 taskId）
3. 公网可访问 + 代码仓库公开

详细能力与页面矩阵见根目录 `README.md`。

---

## 2. 我们干了什么（按阶段）

### 阶段 A · 从司南到明辨（重构）

- 品牌：司南 → **明辨**（司南撞 OpenCompass；数字先知是别人的 MIT 项目，禁用）
- 引擎：`PRIMARY=infini`，每次任务 `settings` 锁模型
- 数据契约：`Evidence / Claim / Gap / Issue / Quality / Tension / Envelope…`（`app/models.py`）
- 七→**八**节点 DAG（后来加上辩论节点）
- 可信度纯规则打分（`app/credibility.py`）
- 质检返工（`app/audit.py` + pipeline 循环）
- 九页面：研判台 / 报告 / 回放 / 图谱 / 指标 / 专家册 / 台账 / Benchmark / 方法论
- 博查 Web Search 接入（`app/collectors/bocha.py`）
- 实体图谱、决策回放、Benchmark 10 题

原始规划全文：`docs/plan.md`（Cursor 计划文件原件）。

### 阶段 B · 把「部分完成」补成「完成」（v1.1）

用户点名：选择性辩论门控、立场演变轨迹当时只是红队+张力，没做成完整机制。已落地：

| 模块 | 文件 | 做什么 |
|---|---|---|
| 辩论门控 | `app/debate.py` | 六信号加权；够分才开辩；红队攻击 + 裁判裁定 |
| 立场轨迹 | `app/stance.py` | 全阶段打点；init/ground/firm/soften/reverse/hold |
| 接线 | `app/pipeline.py` | 门控 → 辩论轮次 → revision → trajectory 进 payload/SSE |
| 前端 | `mb-report.js` / `mb-live.js` | 辩论席、轨迹时间线、实时事件 |
| 取证层分头补采 | `experts.collect_plan` + pipeline | 每位取证专家带自己的检索切口 |
| 带保留通过 | `audit.meets_hard_bar` | 硬指标达标但质检官仍有意见 → `pass_with_notes` |
| 数据填满 | metrics / demos / ledger backfill | 首页统计带、专家出场、台账 162 条 |

### 阶段 C · 部署 / GitHub / 报名

- `deploy/push.sh` + expect 脚本推到服务器并跑 `deploy.sh`
- README 按 v1.1 重写
- 台账从示例/报告/`_runs` 回填（`scripts/backfill_ledger.py`）
- 报名文案：`报名信息.md`

---

## 3. 关键硬约束（违反就会被用户打回来）

1. **报告 / UI / 台账 / 决策回放里禁止出现备用引擎名**  
   内部可以换路，对外只认 InfiniSynapse · deepseek-v4-pro。  
   相关：`pipeline.MODEL_PUBLIC`、`sanitize_engine_name.py`、空实现的 `on_degraded`。

2. **不要用「数字先知」「司南」当产品名**（历史与撞名原因见 `plan.md` 零章）。

3. **不要动 Anker**（8766 / `/anker/`）。部署脚本只能动 8767 与 `/mingbian/`。

4. **示例报告的 `evidence` / `experts` 必须是列表**  
   曾经把它们写成展示用字符串，报告页直接白屏。摘要放 `*_label`。

5. **返工稿解析出 0 条论点时要回退上一版**  
   否则空壳会覆盖好报告。见 `pipeline.py` 的 `prev_good`。

6. **seed 时用 `pipeline.run` 的返回值存盘，不要用 SSE `report` 事件**  
   事件为了省带宽裁掉了 `trace`/`calls`，决策回放会空。

7. **概率夹在 3%–97%**（`models.resolve_probability`），别再出现 100%。

---

## 4. 目录导读（改代码从哪进）

```
app/pipeline.py     # 主流程：取证→成文→质检→辩论→落库→台账
app/debate.py       # 门控信号 + 攻击/裁定解析
app/stance.py       # 轨迹打点
app/audit.py        # 规则质检 + LLM 五维 + meets_hard_bar
app/infini.py       # InfiniSynapse 客户端（锁模型、SSE、认领 taskId）
app/minimax.py      # 仅内部降级，勿在 UI 暴露
app/experts.py      # 16 席 + collect_plan 分头补采
app/demos.py        # 示例列表/归一（防坏形状）
app/store.py        # 报告 + ledger.jsonl
app/bench.py        # 10 题 + 曲线
web/static/mb-*.js  # 前端三件套
deploy/push.sh      # 本机一键推服务器
scripts/seed_demos.py
scripts/backfill_ledger.py
scripts/sanitize_engine_name.py
scripts/smoke_debate.py   # 桩测辩论，不打真引擎
```

数据：

- `data/demos/*.json` —— **进 git**，评委点开的六个案例  
- `data/bench.jsonl` —— Benchmark 曲线，进 git  
- `reports/` —— **不进 git**（本地/服务器运行产物；台账、用户报告、`_runs`）

---

## 5. 本地怎么跑

```bash
cd mingbian-app   # 或 clone 下来的 mingbian
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env   # 填 INFINI_API_KEY；建议填 BOCHA_API_KEY
./.venv/bin/python run.py
# http://127.0.0.1:8767
```

常用：

```bash
./.venv/bin/python scripts/seed_demos.py scam      # 重跑一个示例
./.venv/bin/python scripts/smoke_debate.py         # 辩论桩测
./.venv/bin/python scripts/backfill_ledger.py      # 重建台账
./.venv/bin/python scripts/sanitize_engine_name.py # 扫掉引擎名泄漏
./.venv/bin/python scripts/run_bench.py            # Benchmark
```

---

## 6. 服务器怎么部署

参考备份文档（多项目隔离规范）：

`../20260630_小米黑客松服务器备份/服务器部署说明.md`

本仓库封装：

```bash
export MB_SSH_PASS='…'          # 密码见上述备份文档，勿写进仓库
bash deploy/push.sh             # 打包 → scp → 远程 deploy.sh → 健康检查
```

手动路径：

- 代码目录：`/opt/projects/mingbian`
- 服务：`systemctl status mingbian`
- 日志：`journalctl -u mingbian -f`
- nginx：`/etc/nginx/sites-available/projects` 里 `location /mingbian/`
- hub：`/var/www/hub/index.html`（应有「明辨」卡片）

**注意：** `deploy.sh` rsync **排除** `reports/`，所以台账不会自动上去；改完示例后要单独：

```bash
./deploy/scp.exp reports/ledger.jsonl /opt/projects/mingbian/reports/ledger.jsonl
./deploy/scp.exp data/demos /opt/projects/mingbian/data/
./deploy/ssh.exp 'systemctl restart mingbian'
```

旧路径 `/sinan/` 应 301 到 `/mingbian/`。

---

## 7. 数据与页面「看起来满」的来源

| 页面 | 数据从哪来 |
|---|---|
| 首页统计带 / 案例卡 | `api/demos` + `metrics.corpus_snapshot` |
| 专家册出场数字 | `metrics.expert_usage`（读报告里的 experts 字段） |
| 调用台账 | `reports/ledger.jsonl`；回填脚本从 demos/reports/_runs 重建 |
| Benchmark | `data/bench.jsonl` + `bench.snapshot` |
| 指标页触发条件 | 各报告 `triggers[]` 汇总 |

六个示例当前质检口径多为 **`pass_with_notes`（带保留通过）**：硬指标够，质检官意见还在。这是刻意设计，不是失败。

---

## 8. 已知未竟 / 下一任可接着做

按优先级：

1. **Infini 额度与 taskId 密度**  
   额度打满时内部会换路；换路调用没有平台 taskId。台账会显示「有记录但无回执号」。充值后用真 Infini 再 seed 一轮，台账「可核验」比例会更好看。

2. **jobdd / ai 类问题绑证率**  
   模型爱把「诚实缺口」写成论点 → unsupported。已用 `meets_hard_bar` 放宽，但仍可在 prompt 里禁止把缺口写成 claim。

3. **速判档耗时文案**  
   计划里写过「约 40 秒」，实测深研级也要数分钟；首页档位说明以实测为准，别再写太乐观。

4. **关注清单 / 人工复核**  
   API 在，页面能展示，但评委路径上几乎没人用；若要比「人在闭环」，可预置几条 watch / review。

5. **hub 与报名页**  
   改完功能后记得同步 hub 卡片文案与 `报名信息.md`。

6. **不要再把「部分完成」口头说成「完成」**  
   用户为此追问过一次；能力要么真落地，要么在方法论页写清边界。

---

## 9. 给下一任 AI 的工作方式建议

1. 先读本文 + `README.md` + `plan.md` 文首「落地差异」。  
2. 改引擎/台账相关代码前，先读 `PITFALLS.md`。  
3. 改完跑：`smoke_debate.py` → 本地打开 `/report/scam` → `sanitize_engine_name.py`。  
4. 重跑示例后：回填台账 → `deploy/push.sh` 或 scp demos+ledger → 公网抽查。  
5. 提交前确认：`.env` 不进 git；对外文案无备用引擎名；Anker 健康检查仍 200。

---

## 10. 相关路径速查

| 用途 | 路径 |
|---|---|
| 本仓库（本地） | `/Users/daishanghao/Desktop/20260725_infinisynapse比赛/mingbian-app` |
| 服务器项目 | `/opt/projects/mingbian` |
| 部署说明备份 | `../20260630_小米黑客松服务器备份/服务器部署说明.md` |
| 博查 API 说明 | `../博查api.md` |
| Infini API 速查 | `../Server_API速查表.md` / `../infinisynapse官方api.md` |
| 字节参考项目 | `../20260724_字节全栈AI_竞品分析/` |
| 对话 JSONL | `~/.cursor/projects/.../agent-transcripts/77a01a64-.../....jsonl` |
