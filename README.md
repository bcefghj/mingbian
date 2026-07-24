# 司南 SINAN · 多智能体证据研判引擎

> 派一支**专家评审团**，为你多源取证、**无证据不立论**，把纷杂信息研判成**一个可信的方向**。  
> InfiniSynapse × CSDN「Vibe Coding」泛数据分析应用开发大赛 · 参赛作品。

**在线 Demo：** [http://47.119.112.225/sinan/](http://47.119.112.225/sinan/)  
**代码仓库：** [https://github.com/bcefghj/sinan](https://github.com/bcefghj/sinan)  
**健康检查：** [http://47.119.112.225/sinan/app/healthz](http://47.119.112.225/sinan/app/healthz)

---

## 一句话介绍

**司南**（取名于中国古代第一个指南针）是一个**证据驱动的多智能体研判引擎**。  
你提出一个需要判断的问题——市场概率、职场尽调、打假反诈——司南通过 **InfiniSynapse 官方 Server API** 动态派遣专家团联网取证，坚持「无证据不立论」，抽取实体与隐藏关联，最终产出带**专家团卡片 · 置信度仪表 · 概率场景 · 证据表 · 关联图谱**的可视化报告；可分享、可批注深化、可在 InfiniSynapse 后台用 `taskId` 核验。

---

## 它解决什么问题

| 痛点 | 司南怎么做 |
|------|-----------|
| 网络噪音多、观点互相打架 | 只看可核验证据，不看情绪化观点 |
| 结论空口无凭 | 每条关键结论绑定证据 + 置信度（高/中/低/存疑） |
| 单一视角容易一边倒 | 专家团交叉研判，强制包含「红队反方」主动证伪 |
| 骗局/马甲难发现 | 实体抽取 + 关联图谱，揪团伙式一致性（话术一致、同源收款等） |
| 报告看完没法追问 | 对任意结论批注，一键深化再挖一层 |

**目标用户：** 面对高风险决策、被信息噪音淹没、需要「带证据、可追溯」研判的普通人与专业人士。  
**典型场景：** 买房 / 买金 / 择时、入职前尽调、投资前避坑、识别理财骗局等。

---

## 设计理念：融合三套优秀范式（原创实现）

司南不是套壳，而是把三类获奖作品的 DNA **重新实现**进同一条流水线：

| 来源范式 | 吸收的核心能力 | 在司南中的落地 |
|----------|----------------|----------------|
| 多信号预言 | 只看数据不看观点；多源交叉找共振/背离；给概率 | 信号证据表 + 概率场景 + 整体置信度 |
| 深度研究 Agent（混合检索协作） | 专家评审团；无证据不立论；DAG 流水线；批注深化；结构化可视化 | 7 类专家名册 + `sinan-meta` 驱动 UI + `/api/deepen` |
| 情报分析（Meliora / Argus） | 实体抽取；隐藏关系 / 团伙发现；可监测 | 关联图谱；打假示例中展示马甲一致性 |

**命名说明：** 产品原创命名为「司南 SINAN」，与任何同赛道既有作品名称无关，避免混淆与抄袭嫌疑。

---

## 专家评审团（7 席，按问题动态派遣 3–6 位）

| Key | 专家 | 职责 |
|-----|------|------|
| `market` | 市场定价专家 | 预测市场赔率、期货持仓、期权 IV、利率曲线——用价格反推共识概率 |
| `macro` | 宏观周期专家 | 利率、信贷/GDP、央行政策、领先指标，判断周期位置 |
| `industry` | 行业竞争专家 | 格局、龙头、产能与估值，判断产业景气与拐点 |
| `sentiment` | 舆情情报专家 | 多平台舆情聚类、情绪走向、识别水军/一致性操纵 |
| `entity` | 关联溯源专家 | 抽取实体（人/机构/账号/产品），发现隐藏关系与团伙网络 |
| `risk` | 风险合规专家 | 资质、司法、财务、监管、骗局信号，划定红线 |
| `contra` | 红队反方专家 | **专门证伪**：主动找反例，攻击主流结论，避免一边倒 |

---

## 研判流水线（DAG）

```text
用户提问
   │
   ▼
① 拆解问题（核心变量 / 时间窗口 / 可证据化程度）
   │
   ▼
② 组建专家团（从 7 席中挑 3–6 位，说明派遣理由）
   │
   ▼
③ 多源取证（InfiniSynapse 联网 WebSearch，抽取信号+出处+可信度）
   │
   ▼
④ 关联发现（实体抽取 → 隐藏关系 / 团伙式一致性）
   │
   ▼
⑤ 交叉研判（共振 vs 背离，短/中/长分层，概率场景）
   │
   ▼
⑥ 置信与边界（整体置信度 + 证据缺口诚实声明）
   │
   ▼
输出双通道：
   ├─ Markdown 人类可读报告
   └─ ```sinan-meta``` 结构化 JSON → 前端可视化
         │
         ├─ 结论置信度仪表
         ├─ 专家团卡片（立场色）
         ├─ 概率场景条
         ├─ 证据表（来源 + 置信）
         └─ 关联关系图谱
```

**兜底策略：** 主引擎 `PRIMARY_ENGINE=infini`；InfiniSynapse 超时 / 空报告时自动切换 **MiniMax**，保证演示不断档。

---

## 预置示例（首页零登录可看）

| ID | 标签 | 问题 |
|----|------|------|
| `house` | 楼市 | 中国房价还会跌多久？ |
| `gold` | 黄金 | 现在适合买黄金吗？ |
| `btc` | 加密 | 比特币见底了吗？ |
| `ai` | 科技 | AI 是不是泡沫？ |
| `jobdd` | 职场尽调 | 某公司给了 offer，值不值得入职？ |
| `scam` | 打假反诈 | 这个号称「稳赚不赔、日返 3%」的项目是不是骗局？ |

> 服务器上执行 `seed_demos.py` 可用真实 API 重跑示例，写入真实 `taskId`，便于评委在 InfiniSynapse 后台核验。

---

## InfiniSynapse API 集成（比赛要求）

主引擎严格按官方异步规范：

1. `GET /api/ai/events?connId=<uuid>` —— 先建 SSE 连接  
2. `POST /api/ai/message` + `autoApprovalSettings` / `enableWebSearch: true` —— 开启联网  
3. `POST /api/ai/message` + `newTask` —— 创建任务，拿到 **taskId**  
4. SSE 流式消费 + 轮询 `/api/ai_task/tasks` 兜底取回报告（含 `sinan-meta`）  
5. `POST /api/ai_task/setShare` —— 生成公开可核验链接  
6. 「批注深化」再起一次轻量 `newTask`，顺着用户质疑继续挖  

报告页展示真实 `taskId`，评委可在 [app.infinisynapse.cn](https://app.infinisynapse.cn) 任务后台核验。

实现位置：`app/infini.py`（主引擎 + meta 抽取）、`app/orchestrator.py`（调度）、`app/minimax.py`（兜底）。

---

## 产品界面（情报台）

首页 `web/index.html` 按「情报工作台」设计，而不是普通聊天框：

- **司南罗盘**品牌标识 + 一句话提问入口  
- **结论置信度仪表**（整体概率 / 置信）  
- **专家团卡片**（立场色：看多 / 看空 / 中性 / 存疑 / 高风险）  
- **概率场景条**（多情景对比）  
- **证据表**（信号 · 数据 · 含义 · 来源 · 置信度）  
- **关联关系图谱**（实体节点 + 关系边）  
- **批注深化框**（选中结论 → 质疑 → 再挖一层）  
- **分享页** `web/report.html`（可携带 `taskId` / share 链接）

若模型未按格式输出 `sinan-meta`，前端自动降级为纯 Markdown 报告，不白屏。

---

## 目录结构

```text
sinan/
├── app/
│   ├── main.py            # Starlette 路由 / SSE / deepen
│   ├── orchestrator.py    # 引擎调度（infini → minimax 兜底）
│   ├── infini.py          # InfiniSynapse 官方 API 客户端 + meta 抽取
│   ├── minimax.py         # MiniMax 兜底引擎
│   ├── prompts.py         # 方法论、专家名册、深化 prompt
│   ├── demos.py           # 预置示例读写
│   ├── store.py           # 报告落盘
│   └── envload.py         # .env 加载
├── web/
│   ├── index.html         # 情报台主 UI
│   └── report.html        # 分享 / 报告页
├── data/demos/*.json      # 6 份预置研判（含结构化 meta）
├── deploy/
│   ├── insert_nginx.py    # 幂等插入 nginx location
│   └── nginx-sinan.snippet
├── deploy.sh              # 一键部署（不动 Anker）
├── seed_demos.py          # 用真实 API 重跑示例 + 写 taskId
├── run.py                 # 本地 / systemd 入口
├── requirements.txt
├── .env.example
└── 报名信息.md
```

---

## HTTP API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/healthz` | 健康检查 + 引擎配置状态 |
| `GET` | `/` | 情报台首页 |
| `GET` | `/api/capabilities` | 能力说明 + 专家名册 |
| `GET` | `/api/demos` | 预置示例列表 |
| `GET` | `/api/demo/{id}` | 单个示例完整报告 + meta |
| `POST` | `/api/analyze` | 实时研判（SSE 流式：status / plan / done / error） |
| `POST` | `/api/deepen` | 批注深化（针对某条结论再挖一层） |
| `GET` | `/report/{id}` | 分享报告页 |

`POST /api/analyze` 请求体：

```json
{ "question": "现在适合买黄金吗？" }
```

SSE 事件示例：`status` → `plan` → `done`（含 `markdown` / `meta` / `taskId` / `engine`）。

---

## 本地运行

```bash
git clone https://github.com/bcefghj/sinan.git
cd sinan
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env：填入 INFINI_API_KEY（及可选 MINIMAX_API_KEY）
python run.py
# 浏览器打开 http://127.0.0.1:8767/
```

依赖：`starlette`、`uvicorn`、`httpx`（见 `requirements.txt`）。

---

## 部署到阿里云（多项目隔离，不动 Anker）

本仓库按「一台服务器、多个作品」规范接入为**项目三**：

| 项 | 值 |
|----|-----|
| 目录 | `/opt/projects/sinan` |
| systemd | `sinan.service` |
| 端口 | **8767**（Anker 为 8766，互不冲突） |
| 公网路径 | `/sinan/` → 反代到本机 8767 |
| 安全 | 服务只监听 `127.0.0.1`，外网走 nginx |

`deploy.sh` 行为：

1. 检查 root / nginx / 端口占用  
2. `rsync` 同步代码（保留已有 `.env`，不覆盖密钥）  
3. 建 venv + 装依赖（清华镜像）  
4. 写 systemd 并启动  
5. **备份** nginx 配置 → 幂等插入 `/sinan/` → `nginx -t` 失败则**自动回滚**

```bash
# 方式 A：git clone
ssh root@你的服务器
git clone https://github.com/bcefghj/sinan.git /root/sinan-app
cd /root/sinan-app && sudo bash deploy.sh
# 把真实密钥写入 /opt/projects/sinan/.env 后：
sudo systemctl restart sinan

# 方式 B：本地上传后部署
scp -r . root@47.119.112.225:/root/sinan-app
ssh root@47.119.112.225 'cd /root/sinan-app && sudo bash deploy.sh'

# 强烈建议：用真实 API 生成带 taskId 的示例
cd /opt/projects/sinan && ./.venv/bin/python seed_demos.py
# 只跑部分：./.venv/bin/python seed_demos.py house gold scam
```

上线地址：**http://47.119.112.225/sinan/**

验证 Anker 未受影响：

```bash
systemctl is-active nginx sinan anker
curl -s -o /dev/null -w 'sinan:%{http_code} anker:%{http_code}\n' \
  http://127.0.0.1/sinan/app/ http://127.0.0.1/anker/app/
```

卸载（不影响其它项目）：

```bash
systemctl disable --now sinan.service
# 手动删除 nginx 中 sinan location 段后：
nginx -t && systemctl reload nginx
```

---

## 环境变量（`.env`）

参考 `.env.example`（**切勿把真实 `.env` 提交进 git**）：

| 变量 | 说明 | 默认 |
|------|------|------|
| `PRIMARY_ENGINE` | 主引擎：`infini` / `minimax` | `infini` |
| `INFINI_API_KEY` | InfiniSynapse API Key（比赛主用） | — |
| `INFINI_BASE_URL` | Infini 服务地址 | `https://app.infinisynapse.cn` |
| `INFINI_ENABLE_WEBSEARCH` | 是否开启联网 | `true` |
| `MINIMAX_API_KEY` | MiniMax 兜底 Key | — |
| `MINIMAX_BASE_URL` | MiniMax API | `https://api.minimaxi.com/v1` |
| `MINIMAX_MODEL` | 模型名（报错可换 `MiniMax-M2` / `MiniMax-Text-01` 等） | `MiniMax-M2.7` |
| `PORT` / `HOST` | 监听端口与地址 | `8767` / `127.0.0.1` |
| `ANALYZE_TIMEOUT` | 单次分析超时（秒） | `300` |

---

## `sinan-meta` 结构化约定（前端可视化契约）

模型须在 Markdown 报告之外输出围栏：

````markdown
```sinan-meta
{
  "topic": "问题精简标题",
  "overall": {"verdict": "一句话判断", "probability": 0.55, "confidence": "高|中|低"},
  "experts": [
    {"key":"market","name":"市场定价专家","finding":"...","stance":"看多|看空|中性|存疑|高风险","confidence":"高|中|低"}
  ],
  "signals": [
    {"layer":"第1层","name":"信号名","value":"数据","meaning":"含义","source":"出处","confidence":"高|中|低"}
  ],
  "scenarios": [{"name":"情景","probability":0.55,"basis":"依据"}],
  "entities": [{"name":"实体","type":"人|机构|账号|产品|资产|指标"}],
  "relations": [{"from":"A","to":"B","rel":"关系描述"}]
}
```
````

后端在 `app/infini.py` 中解析该块；前端据此渲染仪表 / 卡片 / 图谱。

---

## 常见问题

| 现象 | 处理 |
|------|------|
| 报告或 meta 为空 | 看 `journalctl -u sinan.service -f`；后端已做 SSE+轮询+多字段容错；无 meta 时前端降级为 Markdown |
| InfiniSynapse 慢 / 超时 | 等待兜底切 MiniMax；或调大 `ANALYZE_TIMEOUT` |
| MiniMax 报模型名错误 | 改 `MINIMAX_MODEL` 为 `MiniMax-M2` / `MiniMax-Text-01` / `abab6.5s-chat` |
| SSE 长时间无输出 | 确认 nginx `proxy_buffering off`（部署脚本已写入） |
| 端口 8767 被占 | `ss -ltnp \| grep 8767` 查占用；勿与 Anker(8766) 混用 |
| 想只更新代码 | 在服务器项目目录重新 `git pull` + `bash deploy.sh`（`.env` 会被保留） |

---

## 技术栈

- **后端：** Python 3 · Starlette · Uvicorn · httpx · asyncio SSE  
- **前端：** 原生 HTML/CSS/JS（无构建、零依赖、情报台布局）  
- **主 AI：** InfiniSynapse Server API（联网 WebSearch）  
- **兜底 AI：** MiniMax  
- **部署：** systemd + nginx 反代 · 多项目子路径隔离  

---

## 合规与声明

- 报告基于**公开证据的概率研判**，**不构成**投资 / 法律 / 医疗建议。  
- 方法论吸收「只看数据」「专家团+证据链」「实体关联发现」等公开优秀范式，为**独立实现**；产品命名「司南 SINAN」为原创。  
- API Key 仅放在服务器 `.env`，已被 `.gitignore` 排除，勿提交仓库。  
- 请遵守 InfiniSynapse 与 MiniMax 各自服务条款及比赛规则。

---

## 作者

戴尚好 · 中国科学技术大学  
GitHub：[@bcefghj](https://github.com/bcefghj) · Email：bcefghj@163.com

---

## License

本参赛作品代码以学习与比赛展示为目的开源；第三方 API 的使用权以各平台条款为准。
