# 企业安全监管平台（RAG 增强）

面向园区企业安全监管的一体化平台：**企业台账管理 + 巡查整改闭环 + 知识库智能问答**。

- **业务系统**：企业档案、巡查整改、厂中厂、危化品、小场所、有限空间、材料库、文书导出、账号权限
- **AI 能力**：MinerU 文档解析 → BGE-M3 双路向量 → Qdrant 混合检索 → Hermes Agent 编排工具链问答

> 📖 **详细文档**（[docs/](docs/)）：
> - [架构总览](docs/架构总览.md) — 容器编排、服务调用关系、环境变量、文件结构
> - [SQL 表结构](docs/sql表结构.md) ｜ [Web 前端代码设计](docs/web前端代码设计.md)
> - [Qdrant 解析与召回](docs/qdrant解析与召回.md) ｜ [MCP 工具调用](docs/mcp工具调用.md) ｜ [Excel 数据导入](docs/Excel数据导入.md)

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      浏览器 / Hermes WebUI                    │
└───────────┬──────────────────────────────┬──────────────────┘
            │ 业务页 HTTP + JWT             │ SSE 智能问答
   ┌────────▼────────┐            ┌─────────▼──────────┐
   │   rag-api       │            │   hermes-webui     │
   │  (FastAPI:8000) │───────────▶│   (Agent:8642)     │
   │  业务 CRUD       │  代理 SSE   │   直连 DeepSeek     │
   └────────┬────────┘            └─────────┬──────────┘
            │                               │ MCP 协议
            │                    ┌──────────▼──────────┐
            │                    │   rag-mcp (:8001)   │
            │                    │  37 个 MCP 工具      │
            │                    └──┬────────┬─────────┘
   ┌────────▼────────┐             │        │
   │   PostgreSQL    │◀────────────┘        │
   │   业务数据 :5432 │                     │
   └─────────────────┘        ┌────────────▼──────────┐
                              │      Qdrant :6333     │
                              │  Dense + Sparse 向量   │
                              └────────────┬──────────┘
                              ┌────────────▼──────────┐
                              │    MinerU :8000       │
                              │    PDF OCR 解析        │
                              └───────────────────────┘

模型层：DeepSeek（生成）｜ BGE-M3（Embedding，Dense+Sparse）｜ BGE-Reranker-v2-m3（精排）
```

**关键边界**：`rag-mcp` 与 `rag-api` 容器内**不调用任何 LLM**，只提供确定性工具/接口；LLM 编排在 Hermes 侧完成。

## 功能模块

### 业务系统

| 模块 | 页面 | 说明 |
|------|------|------|
| 总览看板 | `/dashboard` | 企业数、隐患统计、整改进度、超期预警 |
| 企业台账 | `/enterprises` | 企业档案主表，多维度筛选、列拖拽、批量编辑 |
| 企业档案 | `/enterprise/profile` | 单企业详情：基本信息、关联材料、巡查记录 |
| 巡查整改 | `/inspections` | 隐患登记与整改闭环，双击企业名跳转档案，超期派生状态 |
| 厂中厂 | `/enterprises/factory-in-factory` | 出租方/承租方关系，企业名称纵向合并展示 |
| 危化品 | `/enterprises/hazardous-chemicals` | 企业 × 危化品明细表，"不构成重大危险源"灰字标注 |
| 小场所 | `/enterprises/small-venues` | 小型经营场所专项，安全巡查员/消防指导员信息 |
| 有限空间 | `/enterprises/confined-spaces` | 地上/地下多标签展示 |
| 材料库 | `/materials` | 企业关联材料归档，按 `enterprise_codes` 聚合 |
| 文书导出 | — | 交办单、复查意见书，Word 模板填充后下载 |
| 系统管理 | `/system/staff` `/system/audit` | 员工账号、对话限额配置、审计日志 |
| 智能问答 | `/chat-page` | 多会话流式问答，工具进度展示，按用户隔离 |

### AI / 检索能力

| 能力 | 说明 | 状态 |
|------|------|------|
| 文档解析 | PDF（MinerU / pypdf / auto）、Word、PPT、Excel、Markdown、TXT | ✅ |
| 向量索引 | BAAI/bge-m3 + Qdrant，Dense + Sparse 双向量，支持批量与增量 | ✅ |
| 混合检索 | Dense + Sparse 双路召回 → RRF 融合 → Cross-Encoder 精排 | ✅ |
| MCP 工具服务 | 37 个工具（知识库 11 / 台账只读 6 / Excel 导入与补全 16 / 文书导出 4） | ✅ |
| 多 Collection | 知识库按 Collection 隔离（如 `workfile` / `中医`） | ✅ |
| OCR 质量检测 | MinerU 解析后自动检测乱码，metadata 打 `quality_warning` | ✅ |
| 跨文档聚合 | 多轮检索 → 去重 → Top-K 片段；统计由 Hermes 侧 LLM 完成 | ✅ |
| 对话配额 | 非管理员日费用上限（默认 ¥1.00），超限禁用，管理员可调可锁 | ✅ |

## 服务拓扑

| 服务 | 容器名 | 端口 | 职责 |
|------|--------|------|------|
| 业务后端 | `rag-api` | 8000 | FastAPI：企业/巡查/危化品/厂中厂/材料/导出/认证/聊天 SSE 代理 |
| MCP 工具服务 | `rag-mcp` | 8001 | 37 个 MCP 工具（streamable-http），供 Hermes 编排 |
| 前端 | localhost:8848（vite dev） | 8848 | Vue 3 + Element Plus，代理 `/api` → 8000 |
| 向量库 | `qdrant` | 6333 | Dense + Sparse 双向量存储 |
| 业务库 | `postgres` | 5432 | 8 张业务表 + 员工/会话/审计表 |
| PDF 解析 | `mineru` | 8000 | OCR 与版面分析 |
| Agent 前端 | `hermes-webui` | 8642 | DeepSeek 直连 + MCP 工具编排 |

首次启动 `rag-api` 会自动执行 Alembic 迁移（`upgrade head`）并种入管理员账号，无需手动建表。

## 快速开始

### 前置条件

- Docker & Docker Compose v2
- NVIDIA GPU（可选，MinerU 需要；无 GPU 时设 `PDF_PARSER=pypdf`）
- Node.js ≥ 20.19 + pnpm ≥ 9（前端开发）

```bash
# shared-rag 网络（Hermes 与 rag-api/rag-mcp 互通）
docker network create shared-rag
```

### 启动后端

```bash
cd /home/zshay/rag-project

# 1. 配置环境变量（必须！.env 不入库，模板里有全部说明）
cp .env.example .env

# 2. 编辑 .env，至少填入：
#    LLM_KEYS={"deepseek":"sk-你的密钥"}
#    JWT_SECRET=<openssl rand -base64 48 生成>
#    HERMES_API_KEY=<与 Hermes 侧 API_SERVER_KEY 一致>

# 3. 创建数据目录（首次）
mkdir -p data/uploads data/mineru data/qdrant data/postgres

# 4. 启动
docker compose up -d --build

# 5. 健康检查
curl http://localhost:8000/health    # rag-api
curl http://localhost:8001/mcp       # rag-mcp
```

首次启动会自动完成：建表迁移 → 种入 `admin` 账号 → 后台加载嵌入/重排模型（30-60s）。

> ⚠️ 管理员初始密码见 `app/db/seed.py`，**首次登录后立即修改**。

### 启动前端

```bash
cd web
pnpm install
pnpm dev          # http://localhost:8848
```

生产构建：`pnpm build`，产物在 `web/dist`。

### 导入文档

**方式一：MCP 工具**（推荐，在 Hermes 对话里让 Agent 调用）

`qdrant_file_index`（单文件）/ `qdrant_directory_index`（目录）；导入前可用 `file_parse_preview` 预检。

**方式二：CLI**

```bash
docker exec rag-mcp python -m app.import_docs --dir /app/data/uploads

# 参数：
#   --collection     目标 Qdrant collection，默认取 QDRANT_COLLECTION
#   --no-recursive   不扫描子目录
#   --skip-existing  跳过已索引文件（增量）
#   --replace        删除同源已有数据后重建
```

### 智能问答

`/chat-page` 提问后：前端 SSE → `rag-api:8000` → 透传 `hermes-webui:8642` → Hermes 调 `rag_docs_search` / `sql_*` → 流式返回。

## 项目结构

```
rag-project/
├── app/
│   ├── main.py                 FastAPI 入口（启动迁移 + 种账号 + 模型预热）
│   ├── config.py               环境变量解析与配置中心
│   ├── alembic/                数据库迁移脚本
│   ├── core_Qdrant/            解析、分块、索引、检索、重排
│   ├── db/                     SQLModel 模型、会话、seed
│   ├── mcp/                    MCP 工具（tool_Qdrant / tool_Sql / tool_mix / tool_DocExport）
│   ├── routers/                HTTP 路由（企业/巡查/危化品/厂中厂/材料/导出/聊天/认证）
│   ├── services/               业务服务层（svc_Ledger / svc_Import / svc_Auth / svc_DocExport）
│   └── scripts/                一次性数据维护脚本
├── web/                        Vue 3 + TypeScript + Element Plus 前端
│   ├── src/views/              页面（dashboard / enterprises / inspections / ...）
│   ├── src/components/         共享组件（ReTablePage / ReFilterBuilder / ReContactCell ...）
│   └── src/api/                接口封装
├── docs/                       设计与结构文档
├── data/                       运行时数据（不入库）
├── models/                     模型缓存（不入库）
├── 企业信息文件/                业务源数据（不入库）
├── docker-compose.yml          服务编排
├── Dockerfile                  后端镜像
├── requirements.txt            Python 依赖
└── .env.example                环境变量模板
```

## 环境变量

完整清单与分类说明见 [.env.example](.env.example)（标注了哪些被 compose 硬编码、哪些可覆盖）。

### 核心参数

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `QDRANT_COLLECTION` | `workfile` | 默认知识库 Collection（代码内兜底 `rag_documents`） |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | 嵌入模型 |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | 精排模型 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `512` / `50` | 分块粒度 |
| `PDF_PARSER` | `mineru` | `mineru` / `pypdf` / `auto` |
| `EXCLUDE_PATHS` | 未设置 | 跳过索引的目录，逗号分隔（如 `不解析文件`） |

### 检索参数

| 变量 | 默认值 | 控制什么 |
|------|--------|----------|
| `RETRIEVAL_TOP_K` | `30` | Dense/Sparse 每路召回条数 |
| `RRF_DENSE_WEIGHT` / `RRF_SPARSE_WEIGHT` | `1.0` / `1.0` | RRF 融合权重 |
| `RERANK_TOP_K` | `5` | 重排后最终返回条数 |
| `RERANK_BATCH_SIZE` | `16` | 重排批大小 |
| `AGGREGATE_TOP_K` | `30` | 跨库聚合返回条数 |
| `HYDE_ENABLED` | `false` | HyDE 查询扩展（多一次 LLM 调用） |

### 模型与外部服务

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_PROVIDER` | `ollama` | `ollama` / `api` |
| `OLLAMA_DEFAULT_MODEL` | `qwen3:8b` | 本地模型 |
| `API_DEFAULT_MODEL` | `deepseek-chat` | 远程模型 |
| `API_DEFAULT_BASE` | `https://api.deepseek.com` | 远程 API 地址 |
| `LLM_KEYS` | `{}` | JSON 映射，如 `{"deepseek":"sk-xxx"}` |
| `HERMES_API_BASE` | `http://hermes-webui:8642` | Hermes API Server |
| `HERMES_API_KEY` | — | 须与 Hermes 侧 `API_SERVER_KEY` 一致 |
| `MINERU_HOST` / `MINERU_PORT` | `mineru` / `8000` | MinerU 服务 |
| `MINERU_TIMEOUT` | `600` | 单次解析超时（秒） |

### 数据库与认证

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PG_USER` / `PG_PASSWORD` / `PG_DB` | `rag` / `rag_secret` / `rag` | PostgreSQL 连接 |
| `PG_POOL_SIZE` | `10` | 连接池大小 |
| `JWT_SECRET` | — | **必填**，`openssl rand -url 48` 生成 |
| `JWT_EXPIRE_HOURS` | `24` | Token 有效期 |
| `RAG_API_KEY` | `` | 接口鉴权，留空不启用 |

### 对话计费与配额

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CHAT_PRICE_INPUT_PER_M` | `2` | 输入单价（元/百万 token） |
| `CHAT_PRICE_OUTPUT_PER_M` | `8` | 输出单价 |
| `CHAT_DAILY_LIMIT_DEFAULT` | `1.0` | 非管理员日限额（元），员工可单独覆盖 |

## MCP 工具清单（37 个）

| 分组 | 数量 | 工具 |
|------|------|------|
| 知识库检索 | 11 | `rag_docs_search` `rag_docs_aggregate` `qdrant_docs_list` `qdrant_doc_content_get` `qdrant_doc_info_get` `qdrant_doc_delete` `qdrant_file_index` `qdrant_directory_index` `qdrant_index_status_get` `qdrant_collections_list` `file_parse_preview` |
| 台账只读 | 6 | `sql_enterprises_query` `sql_inspection_records_query` `sql_inspection_stats_get` `sql_inspection_overdue_list` `sql_materials_query` `sql_dashboard_summary_get` |
| Excel 导入与补全 | 16 | `excel_sheets_list` `excel_sheet_inspect` `excel_workbook_plan` `excel_sheet_parse` `excel_sheets_to_pool_read` `pool_normalize` `sql_record_preview` `sql_record_commit` `sql_record_flag` `import_report_get` `sql_enterprises_backfill` `enterprise_field_schema_get` `sql_enterprise_fields_commit` `web_enterprise_code_lookup` `web_enterprises_code_backfill` `web_enterprise_code_commit` |
| 文书导出 | 4 | `doc_export_notice_check` `doc_export_notice` `doc_export_review_check` `doc_export_review` |

**读写边界**：检索类纯只读；Excel 写入走 `preview → commit` 两阶段确认；删除类需 `confirm=true` 二次调用。

## 检索架构

```
查询 → HyDE(可选) → Dense + Sparse 各 30 条 → RRF 融合 → Rerank 取 Top 5 → 片段返回
                                                                    ↓
                                                         Hermes 侧 LLM 生成回答
```

服务端只负责召回与精排，生成在 Hermes 完成。详见 [Qdrant 解析与召回](docs/qdrant解析与召回.md)。

## 开发

```bash
# 后端本地运行（依赖服务走容器）
pip install -r requirements.txt
docker compose up -d qdrant postgres mineru
uvicorn app.main:app --reload --port 8000

# 前端
cd web && pnpm dev

# 代码检查
ruff check app/ --fix
cd web && pnpm typecheck && pnpm lint:eslint
```

### 数据库迁移

```bash
# 新增迁移
docker exec -it rag-api alembic revision --autogenerate -m "描述"

# 应用迁移（启动时也会自动执行）
docker exec -it rag-api alembic upgrade head
```

### 故障排查

| 问题 | 可能原因 | 排查方式 |
|------|----------|----------|
| 模型下载慢 | HuggingFace 网络不畅 | 设 `HF_ENDPOINT=https://hf-mirror.com` |
| MinerU 解析失败 | 无 GPU / 显存不足 | 设 `PDF_PARSER=pypdf` 降级 |
| 无法连接 Ollama | 宿主机防火墙 | `curl http://localhost:11434/api/tags` 验证 |
| Qdrant 连接失败 | 容器未启动 | `docker compose ps qdrant`；本地开发需 `QDRANT_HOST=localhost` |
| Hermes 看不到 MCP 工具 | 网关缓存旧清单 / rag-mcp 未就绪 | 确认 rag-mcp healthy 后重启 Hermes 网关 |
| 首次查询慢 | 模型后台加载中 | `docker compose logs rag-mcp`，等待预热 30-60s |
| GPU 显存不足 | Embedding + Reranker 同时加载 | 两模型约 3-4GB；可注释 rag-mcp 的 `deploy.resources` 改走 CPU |
| 检索结果不准 | 分块过大或解析质量差 | 调小 `CHUNK_SIZE`、增大 `RERANK_TOP_K`、检查 `quality_warning` |
| Hermes 连不上 | 容器不在同一网络 | `docker network connect shared-rag hermes-webui` |
| 登录后立即掉线 | `JWT_SECRET` 变更 | 更换密钥会使所有旧 token 失效，需重新登录 |
| 对话提示额度不足 | 触发日限额 | 管理员在「系统管理 → 员工」调整限额或解锁 |
| 检索不到刚建的知识库 | Collection 名不匹配 | 索引时的 `collection` 与检索时须一致 |
| 启动报 `could not select device driver` | Docker 未配 GPU | 装 `nvidia-container-toolkit`，或注释 `deploy.resources` |

## 许可证

Internal Use Only
