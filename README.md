# 司南 SINAN · 多智能体证据研判引擎

> 派一支**专家评审团**，为你多源取证、**无证据不立论**，把纷杂信息研判成**一个可信的方向**。
> InfiniSynapse × CSDN「Vibe Coding」泛数据分析应用开发大赛 参赛作品。

司南（古代指南针）是一个**证据驱动的多智能体研判引擎**。你提一个需要判断的问题——市场概率（"房价还跌多久""比特币见底了吗"）、职场尽调（"这家公司值不值得入职"）、乃至打假反诈（"这个稳赚项目是不是骗局"）——司南通过 **InfiniSynapse 官方 Server API** 动态派遣多位领域专家联网取证，每条结论绑定证据与置信度，抽取实体、发现隐藏关联，最终产出带**专家团研判 + 概率场景 + 证据表 + 关联图谱**的可视化报告，可一键分享、可批注深化、可在 InfiniSynapse 后台按 `taskId` 核验。

## 设计：融合三套获奖打法（原创实现，非套用）
- **多信号预言的内核**：只看数据不看观点，多源交叉找共振/背离，给概率。
- **深度研究 Agent（08 混合检索）**：专家评审团 + 「无证据不立论」结论绑定证据 + 置信度 + DAG 流水线 + 批注驱动深化 + 结构化可视化报告。
- **情报分析（07 Meliora/Argus）**：实体抽取 + 隐藏关系/团伙式一致性发现（打假场景可揪出马甲话术一致、同源收款等）。

## 它怎么用 InfiniSynapse（比赛集成说明）
主引擎 = InfiniSynapse 官方 Server API（`PRIMARY_ENGINE=infini`），严格按官方异步规范：
1. `GET /api/ai/events?connId=<uuid>` 先建 SSE；
2. `POST /api/ai/message {autoApprovalSettings, enableWebSearch:true}` 开联网；
3. `POST /api/ai/message {newTask,...}` 创建任务拿 **taskId**；
4. SSE 流式 + 轮询 `/api/ai_task/tasks` 兜底取报告（报告含 `sinan-meta` 结构化 JSON 供前端可视化）；
5. `POST /api/ai_task/setShare` 生成公开可核验链接；
6. 「批注深化」用一次新的轻任务，顺着用户批注再深挖。
报告页展示真实 taskId，评委可在 `app.infinisynapse.cn/tasks` 后台核验。**MiniMax 兜底**：InfiniSynapse 卡住自动切换。

## 目录结构
```
sinan-app/
├── app/  main.py(路由) infini.py(主引擎+meta抽取) minimax.py(兜底)
│         orchestrator.py prompts.py(方法论+专家名册) demos.py store.py envload.py
├── web/  index.html(情报台UI) report.html(分享页)
├── data/demos/*.json   预置示例研判（含结构化 meta，评委一打开即见完整证据链）
├── seed_demos.py       服务器上用真实 API 重跑，生成带真实 taskId 的示例
├── deploy.sh + deploy/ 一键部署（项目三/8767/独立 nginx，不动 Anker）
├── requirements.txt .env.example run.py
```

## 部署到阿里云（一条命令，不动 Anker）
作为**项目三**接入：`/opt/projects/sinan` + venv + `sinan.service`(端口 **8767**，Anker 是 8766) + nginx `/sinan/`。改 nginx 前自动备份、`nginx -t` 失败自动回滚。
```bash
# 1) 上传
scp -r sinan-app root@47.119.112.225:/root/     # 或 git clone
# 2) 部署
ssh root@47.119.112.225
cd /root/sinan-app && sudo bash deploy.sh
# 3) 生成"跑好的数据"（真实 API + 真实 taskId，强烈建议）
cd /opt/projects/sinan && ./.venv/bin/python seed_demos.py
```
上线：**http://47.119.112.225/sinan/**

## 配置（.env）
`INFINI_API_KEY`（主用，已填）、`PRIMARY_ENGINE=infini`；`MINIMAX_*` 兜底（若报错多为模型名，可换 `MiniMax-M2`/`MiniMax-Text-01`/`abab6.5s-chat`）。

## 常见问题
- **报告/meta 为空**：InfiniSynapse 消息结构可能有版本差异，后端已「SSE 流式 + 轮询兜底 + 多字段容错」；`journalctl -u sinan.service -f` 看日志。若模型未按格式输出 `sinan-meta`，前端会自动降级为只显示 Markdown 报告。
- **SSE 被缓冲**：nginx 段已 `proxy_buffering off`。
- **卸载**：`systemctl disable --now sinan.service` + 删 nginx 里 sinan 段 + `nginx -t && systemctl reload nginx`，Anker 不受影响。

## 合规
方法论参考开源 digital-oracle（MIT）的"只看数据"思路，为**独立实现**的多智能体研判应用（原创命名「司南」），分析内核通过 InfiniSynapse Server API 完成。报告为公开证据的概率研判，不构成投资/法律/医疗建议。
