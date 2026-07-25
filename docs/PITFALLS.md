# 踩坑清单（明辨）

按「症状 → 根因 → 现况/防法」写。下一任改相关代码前建议通读一遍。

---

## 1. 服务器端口被旧司南占着

**症状：** `deploy.sh` 起服务时报 `address already in use 8767`。  
**根因：** 同槽位以前跑 `sinan.service`，没停干净。  
**防法：** `deploy.sh` 开头会 stop/disable `sinan.service`，必要时 `fuser -k 8767/tcp`。只动 8767，不动 8766。

---

## 2. nginx `duplicate location "/sinan"`

**症状：** `nginx -t` 失败，reload 中断。  
**根因：** 新 snippet 带 `/sinan` 301，旧 conf 里还有 `/sinan` location。  
**防法：** `deploy/insert_nginx.py` 会先撕掉旧 sinan 块再插入；失败自动回滚备份。

---

## 3. 报告页整页「报告不存在」——其实是渲染崩了

**症状：** `/report/scam` 显示找不到报告，但 API 有 JSON。  
**根因：** `seed_demos.py` 曾把 `evidence` / `experts` **列表覆盖成展示字符串**，前端 `.forEach` 炸了，被 catch 成「不存在」。  
**防法：**

- seed 只写 `evidence_label` / `experts_label`
- `demos.normalize` 遇到非 list 降级为空 list，宁可少一块也不白屏

---

## 4. 决策回放永远 0 步

**症状：** 示例的 `/trace/scam` 显示没有 Trace。  
**根因：** seed 存的是 SSE `report` 事件载荷，为了省带宽裁掉了 `trace`/`calls`。  
**防法：** seed 必须用 `await pipeline.run(...)` 的**返回值**落盘。

---

## 5. 对外口径泄漏备用引擎名

**症状：** 决策回放 span、台账 model、报告某处出现备用通道模型名。  
**根因：** 额度打满时内部换路，返回值/span 直接写了底层 `model`。  
**用户硬性要求：** 报告里不要说用了备用引擎。  
**防法：**

- `pipeline.MODEL_PUBLIC` 统一对外模型名
- span decision 不写引擎商品名；错误信息 `_safe_err` 脱敏
- `scripts/sanitize_engine_name.py` 扫历史数据
- 前端 `on_degraded` 空实现

---

## 6. Infini 事件流串台

**症状：** A 问题的报告写进 B 的示例。  
**根因：** SSE 多任务消息混流，曾按「最后一条」认领。  
**防法：** `infini.py` 按 `taskId`（含子智能体 `_delegate_` 前缀）认领；seed **默认串行**。

---

## 7. 返工把好报告改成空壳

**症状：** 返工后 claims=0，首页卡「0/0 论点有据」。  
**根因：** 模型返工时不按 `mb-meta` JSON 回，甚至回 YAML。  
**防法：** `prev_good`——新版解析不出论点就回退上一版，并记一条质检问题；seed 若 0 论点则不覆盖旧文件。

---

## 8. 质检永远「未达标」

**症状：** 六个示例全是黄标「质检未达标」，像废品。  
**根因：**

1. `cross_validation < 60` 也曾触发返工——公开检索补不出第二个独立源，返工白烧  
2. 额度用满后仍标 `rework`，哪怕硬指标已过  
3. 少量「诚实缺口」被写成 unsupported 论点，`unsupported==0` 一刀切

**防法：**

- 交叉验证低分只记 low issue，不单独触发返工  
- 额度用满 + `meets_hard_bar` → `pass_with_notes`（带保留通过）  
- 未绑证允许 ≤30% 且绑证率 ≥70%

---

## 9. 辩论概率改了但最终还是 100% / 标签截断

**症状：** 辩论 Δ=-0.03，结论仍显示 100%；或立场变成「看空（但论证…」半截。  
**根因：** 概率用加法后未夹取；裁判爱写带括号的长立场。  
**防法：** `resolve_probability` 夹 3%–97%；`_clean_stance` 只收词表内立场词。

---

## 10. 专家册取证层全是 0 条证据

**症状：** 只有舆情专家有证据，其余取证专家「从没干过活」。  
**根因：** 泛检索全记在一个人名下，没有分头补采。  
**防法：** `experts.COLLECT_ANGLES` + `collect_plan`，pipeline 里每位取证专家单独 search+verify，`collected_by` 归属。

---

## 11. Benchmark / 台账重复或空壳

**症状：** 曲线重复点；服务器台账 0 条。  
**根因：**

- demo id（`scam`）与 report hex id 并存，去重键太弱  
- `deploy.sh` 不同步 `reports/`，台账文件上不了服务器

**防法：**

- `bench.already_recorded` 用 `run_id` / `taskId` / `elapsed_ms`  
- 部署后跑 `backfill_ledger.py` 并 **单独 scp** `ledger.jsonl`

---

## 12. 沙箱推不了 GitHub / SSH

**症状：** 早期环境 `Host not in allowlist`、SSH 被挡。  
**现况：** 当前 Cursor 环境一般可 `git push`；服务器用本机 `expect` + `MB_SSH_PASS`。  
**防法：** 密码只走环境变量；`deploy/ssh.exp` / `scp.exp` 不写死密码。

---

## 13. sshpass 没有、expect 引号坑

**症状：** `invalid command name "567"` 之类。  
**根因：** expect 的 `spawn` 里 shell 通配/方括号被 expect 解析。  
**防法：** 远程命令尽量简单；复杂逻辑写成远程 heredoc/脚本文件再执行。

---

## 14. 博查主通道挂了不说

**症状：** 可信度普遍偏低，用户不知道为什么。  
**防法：** 全走 fallback 时要在思维流里说明「主通道不可用，已降级到 HTML 抓取，发布时间可能缺失」——这是取证通道降级，**不是**引擎品牌降级，可以说。

---

## 15. 「完成了」但其实是部分完成

**症状：** 用户拿 16 项清单对质：辩论门控、立场轨迹当时只有红队+张力。  
**教训：** 能力要么真做成可演示的独立模块（有门控分、有轨迹点、有 SSE、有报告区块），要么在方法论里承认边界。不要用近义词糊弄「完成」。
