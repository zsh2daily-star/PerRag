# RAG 文档问答系统

基于检索增强生成（RAG）的文档智能问答系统，支持 PDF/Word/PPT/Excel/Markdown 等多种格式文档的自动解析、向量化存储、混合检索与大模型问答。

> 📖 **详细文档**（[docs/](docs/)）：
> - [架构总览](docs/架构总览.md) — 容器编排、服务调用关系、环境变量、文件结构
> - [SQL 表结构](docs/sql表结构.md) ｜ [Web 前端代码设计](docs/web前端代码设计.md)
> - [Qdrant 解析与召回](docs/qdrant解析与召回.md) ｜ [MCP 工具调用](docs/mcp工具调用.md)

## 架构概览

```
问答链路（Hermes 编排）
用户交互层        ┌──────────────┐
                 │ Hermes WebUI │
                 │ (智能问答前端) │
                 └──────┬───────┘
                        │  直连 deepseek + MCP 工具调用
MCP 工具服务     ┌──────▼────────┐
              │    rag-mcp     │  MCP server（streamable-http），30 个工具
              │  ├─ 台账只读    │  企业/巡查/统计/总览（PostgreSQL）
              │  ├─ Excel 导入  │  解析→预览→提交（受控写）
              │  ├─ 企业补全    │  Excel 批量 / 文档语义单企业
              │  └─ 知识库检索  │  混合检索（Dense+Sparse→RRF→Rerank）
              │     索引/删除   │  解析→分块→双向量写入 / 两阶段确认删除
              └────────┬───────┘
                       │
AI 模型层      ┌───────▼────────┐
         ┌────▼────┐   ┌───────▼─────┐  ┌─────────────────┐
         │ DeepSeek│   │   BGE-M3    │  │  BGE-Reranker   │
         │ (远程API)│   │ (Embedding) │  │  (Cross-Encoder) │
         └─────────┘   │  Dense+     │  │  重排精排        │
                       │  Sparse     │  └─────────────────┘
                       └─────────────┘
                       │
数据存储层     ┌────────┼────────┐
              │                 │
         ┌────▼────┐   ┌───────▼─────┐
         │  Qdrant │   │   MinerU    │
         │(向量数据库)│  │  (PDF解析)  │
         └─────────┘   └─────────────┘

业务链路（确定性 CRUD）：台账浏览器(Vue3) ──HTTP+JWT──▶ rag-api:8000 ──▶ PostgreSQL
```

## 核心特性

| 模块 | 说明 | 状态 |
|------|------|------|
| **文档解析** | 支持 PDF（MinerU/pypdf/auto）、Word、PPT、Excel、Markdown、TXT 等多种格式 | ✅ 已实现 |
| **向量化索引** | BAAI/bge-m3 嵌入 + Qdrant 向量数据库，Dense + Sparse 双向量，支持批量索引与增量更新 | ✅ 已实现 |
| **双路混合检索** | Dense（语义向量）+ Sparse（BGE-M3 词权重）双路召回，RRF 融合排序 | ✅ 已实现 |
| **重排优化** | bge-reranker-v2-m3 Cross-Encoder 对召回结果精排 | ✅ 已实现 |
| **MCP 工具服务** | 30 个工具（台账只读 6 / Excel 导入 8 / 企业补全 5 / 知识库 11）暴露为标准 MCP（rag-mcp），供 Hermes Agent 统一编排 | ✅ 已实现 |
| **多 Collection 支持** | 知识库可分 Collection 管理（如 `workfile` / `中医`） | ✅ 已实现 |
| **OCR 质量检测** | MinerU 解析后自动检测乱码，在 metadata 打 quality_warning 标记 | ✅ 已实现 |
| **智能问答** | 平台自建问答页（`/chat`）经 rag-api SSE 代理到 Hermes API Server（8642），由 Hermes 经 MCP 工具链检索 + 生成；多会话历史落 PostgreSQL | ✅ 已实现（2026-10 改为原生 UI + API 代理） |
| **知识库文档管理** | 文档清单/检索/索引/删除全部通过 MCP 工具（`qdrant_docs_list`、`qdrant_doc_delete` 等），rag-api 仅保留材料表内文件的佐证取原文 `/evidence/{id}` | ✅ 已迁移至 MCP |
| **跨文档聚合检索** | `rag_docs_aggregate` 多轮检索 + 汇总去重返回 Top-K 纯文本片段；全局统计分析由 Hermes 侧 LLM 完成（服务端不再调 LLM） | ✅ 已实现（检索与生成分离） |
| **文档列表缓存** | 启动时构建，内存读取零 QPS，索引变更自动刷新 | ✅ 已实现 |

## 服务拓扑

```
┌──────────────┐     ┌──────────────┐
│ 平台问答页    │     │  DeepSeek    │
│ /chat (8848) │────▶│  (Hermes 侧) │
└──────┬───────┘     └──────────────┘
       │ SSE: rag-api:8000 → hermes-webui:8642
       │ Hermes 发起 MCP 协议
┌──────▼───────┐     ┌──────────────┐     ┌──────────────┐
│   rag-mcp    │────▶│   Qdrant     │────▶│  PostgreSQL  │
│  (MCP:8001)  │     │  (向量:6333) │     │  (业务:5432) │
└──────┬───────┘     └──────────────┘     └──────────────┘
       │  PDF 解析
┌──────▼───────┐
│    MinerU    │
│  (端口:30001) │
└──────────────┘

（业务台账页面的浏览器请求另走 rag-api:8000 → PostgreSQL，见架构总览）
```

> **说明**：Hermes WebUI 直连 DeepSeek，检索与业务查询通过 MCP 调用 rag-mcp（`rag-mcp:8001/mcp`）→ Qdrant / PostgreSQL。rag-mcp 容器内不调用任何 LLM，只提供纯工具能力。MinerU 提供 PDF OCR 解析。

## 快速开始

### 前置条件

- **Docker** & **Docker Compose** v2
- **Ollama** 运行在宿主机（Docker 外），已拉取模型：

```bash
# 宿主机上安装并启动 Ollama
ollama pull qwen3:8b

# 确认 Ollama 监听 11434 端口
curl http://localhost:11434/api/tags
```

- **shared-rag 网络**（用于连接其他容器）：

```bash
docker network create shared-rag
```

- **NVIDIA GPU**（可选，MinerU 解析需要。无 GPU 时可改用 `PDF_PARSER=pypdf`）

### 一键启动

```bash
# 1. 进入项目目录
cd /home/zshay/rag-project

# 2. （首次）创建数据目录
mkdir -p data/uploads data/mineru data/qdrant

# 3. 配置环境变量（可选，复制后按需修改）
cp .env .env.local

# 4. 构建并启动所有服务
docker compose up -d --build

# 5. 查看日志
docker compose logs -f rag-mcp

# 6. 健康检查（rag-mcp MCP 端点）
curl http://localhost:8001/mcp
```

### 导入文档

**方式一：MCP 工具**（在 Hermes 对话里让 Agent 调用）

在对话里让 Hermes 调用 `qdrant_file_index`（单个文件）或 `qdrant_directory_index`（目录）工具即可导入；导入前可先用 `file_parse_preview` 预检。

**方式二：CLI 工具**

```bash
# 进入容器后使用 CLI 导入（更适合批量场景）
docker exec rag-mcp python -m app.import_docs --dir /app/data/uploads

# 索引到指定 collection
docker exec rag-mcp python -m app.import_docs \
  --dir /app/data/uploads --collection my_custom_collection

# 可选参数：
#   --collection     目标 Qdrant collection，不传使用 QDRANT_COLLECTION 环境变量
#   --no-recursive   不扫描子目录
#   --skip-existing  跳过已索引文件，仅导入新增（增量导入）
#   --replace        删除已有同源数据后重建（默认追加模式）
```

### 开始提问

在平台"智能问答"页（`/chat`）直接提问：

1. 前端以 SSE 流式请求 rag-api（平台 JWT 鉴权），rag-api 透传到 Hermes API Server（`http://hermes-webui:8642`）
2. Hermes Agent 通过 MCP 连接 rag-mcp（`http://rag-mcp:8001/mcp`）
3. 提问时 Hermes 自动调用 `rag_docs_search` / `sql_*` 等工具检索知识库和业务库，再流式生成回答；支持多会话、工具进度展示、中途停止

## 项目文档

现行文档在 [docs/](docs/)：

- **[架构总览](docs/架构总览.md)**：容器编排、服务调用关系、环境变量、文件结构
- **[SQL 表结构](docs/sql表结构.md)**：8 张业务表、关系、枚举、迁移链
- **[Web 前端代码设计](docs/web前端代码设计.md)**：路由、布局机制、页面、共享组件、SSE
- **[Qdrant 解析与召回](docs/qdrant解析与召回.md)**：MinerU 解析、双路向量索引、RRF + 重排召回
- **[MCP 工具调用](docs/mcp工具调用.md)**：rag-mcp 30 个工具清单、读写边界、Excel 导入链路
- **[Excel 数据导入](docs/Excel数据导入.md)**：四层写入架构、Sheet 分类与列映射、企业补全两条链路

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `QDRANT_HOST` | `qdrant` | Qdrant 服务主机（本地: localhost，容器: qdrant） |
| `QDRANT_PORT` | `6333` | Qdrant HTTP 端口 |
| `QDRANT_COLLECTION` | `rag_documents` | 默认 Collection 名称 |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | 嵌入模型（HuggingFace 名或本地路径） |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | Cross-Encoder 重排模型 |
| `CHUNK_SIZE` | `512` | 文档分块 token 数 |
| `CHUNK_OVERLAP` | `50` | 相邻块重叠 token 数 |
| `IMPORT_DIR` | `data/uploads` | 默认导入目录 |
| `PDF_PARSER` | `mineru` | PDF 解析器（mineru / pypdf / auto） |
| `MINERU_HOST` | `mineru` | MinerU 服务主机（本地: localhost，容器: mineru） |
| `MINERU_PORT` | `30001` | MinerU 服务端口 |
| `MINERU_BACKEND` | `pipeline` | MinerU 解析后端 |
| `MINERU_LANG` | `ch` | MinerU OCR 语言 |
| `MINERU_TIMEOUT` | `600` | 单次 PDF 解析超时（秒） |
| `OLLAMA_HOST` | `host.docker.internal` | Ollama 服务地址（本地: localhost，容器内: host.docker.internal） |
| `OLLAMA_PORT` | `11434` | Ollama 服务端口 |
| `OLLAMA_DEFAULT_MODEL` | `qwen3:8b` | 默认 Ollama 模型 |
| `LLM_PROVIDER` | `ollama` | LLM 后端（ollama / api） |
| `API_DEFAULT_MODEL` | `deepseek-chat` | 远程 API 默认模型 |
| `API_DEFAULT_BASE` | `https://api.deepseek.com` | 远程 API 地址 |
| `LLM_KEYS` | `{}` | JSON 格式的 API Key 映射，如 `{"deepseek":"sk-xxx","openai":"sk-yyy"}` |
| `RETRIEVAL_TOP_K` | `30` | 双路检索每路召回数 |
| `RERANK_TOP_K` | `5` | 重排后保留条数 |
| `RERANK_BATCH_SIZE` | `16` | 重排批处理大小 |
| `AGGREGATE_TOP_K` | `30` | aggregate 模式单次检索返回条数 |
| `RRF_DENSE_WEIGHT` | `1.0` | Dense 路在 RRF 融合中的权重 |
| `RRF_SPARSE_WEIGHT` | `1.0` | Sparse 路在 RRF 融合中的权重 |
| `HYDE_ENABLED` | `false` | 是否启用 HyDE 查询扩展 |
| `CHUNK_METHOD` | `fixed` | 分块策略（fixed / semantic） |
| `MULTIMODAL_ENABLED` | `false` | 是否启用 MinerU 图片提取 |
| `RAG_API_KEY` | `` | API 鉴权密钥，留空不启用 |
| `HF_HOME` | `/app/models` | HuggingFace 模型缓存目录 |
| `RAG_MCP_PORT` | `8001` | RAG MCP 检索服务对外端口 |

> **注意**：上表展示的是 Docker Compose 环境下的默认值。本地开发时，`app/config.py:119` 中的默认值有所不同（如 `QDRANT_HOST=localhost`、`OLLAMA_HOST=localhost`），Docker Compose 中的 `environment` 配置会覆盖这些默认值。

## 检索架构

服务端检索链路：**HyDE（可选）→ Dense + Sparse（各 30 条）→ RRF → Rerank → Top K 片段返回**；片段取回后的 LLM 生成在 Hermes 侧完成。

检索能力通过 MCP 暴露（rag-mcp），由 Hermes Agent 在对话中编排调用；服务端工具本身不调用 LLM。

详见 **[Qdrant 解析与召回](docs/qdrant解析与召回.md)** 与 **[MCP 工具调用](docs/mcp工具调用.md)**。

### 检索参数速查

| 参数 | 默认值 | 控制什么 |
|------|--------|----------|
| `RETRIEVAL_TOP_K` | `30` | Dense/Sparse 每路召回条数 |
| `RRF_DENSE_WEIGHT` | `1.0` | Dense 在融合中的权重 |
| `RRF_SPARSE_WEIGHT` | `1.0` | Sparse 在融合中的权重 |
| `RERANK_TOP_K` | `5` | 重排后最终返回条数 |
| `RERANK_BATCH_SIZE` | `16` | 重排批处理大小 |
| `AGGREGATE_TOP_K` | `30` | aggregate 模式单次检索返回条数 |
| `HYDE_ENABLED` | `false` | 是否启用 HyDE 查询扩展 |
| `CHUNK_SIZE` | `512` | 文档分块大小（影响检索粒度） |

## 开发

### 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动必要的依赖服务（Qdrant + MinerU）
docker compose up -d qdrant mineru

# 启动 FastAPI 开发服务器
uvicorn app.main:app --reload --port 8000

# 或者使用 CLI 工具直接导入文档
python -m app.import_docs --dir /path/to/docs
python -m app.import_docs --dir /path/to/docs --collection my_collection
```

### 代码质量

```bash
pip install ruff
ruff check app/ --fix
```

### 故障排查

| 问题 | 可能原因 | 排查方式 |
|------|----------|----------|
| 模型下载慢 | HuggingFace 网络不畅 | 设置 `HF_ENDPOINT=https://hf-mirror.com` 使用镜像 |
| MinerU 解析失败 | 无 GPU 或 GPU 内存不足 | 设置 `PDF_PARSER=pypdf` 降级为纯文字解析 |
| 无法连接 Ollama | 宿主机防火墙或网络问题 | 检查 `OLLAMA_HOST` 配置，确认 `curl http://localhost:11434/api/tags` 可通 |
| Qdrant 连接失败 | 容器未启动或网络不通 | `docker compose ps qdrant` 确认状态，本地开发需设置 `QDRANT_HOST=localhost` |
| Hermes 看不到 MCP 工具 | 网关缓存了旧工具清单或 rag-mcp 未就绪 | 确认 rag-mcp healthy 后重启 Hermes 网关（连续 3 次连接失败会进入 parked） |
| 首次查询很慢 | 模型正在后台加载 | 查看 `docker compose logs rag-mcp` 等待预热完成（约 30-60s） |
| GPU 内存不足 | 同时加载 Embedding + Reranker 模型 | 两个模型共约 3-4GB 显存，确保 GPU 有足够可用内存 |
| 检索结果不准 | 文档未正确分块或索引 | 减小 `CHUNK_SIZE`、增大 `RERANK_TOP_K`，或检查文档解析质量 |
| Hermes WebUI 连接失败 | 容器不在同一网络 | `docker network connect shared-rag hermes-webui` |
| 流式输出乱码 | 终端编码问题 | 确保终端 UTF-8 编码，或使用非流式接口 |
| collection 不存在 | 尚未导入文档 | 先通过 `/index/directory` 导入文档，再提问 |
| 启动报错 "could not select device driver" | Docker 未配置 GPU | 安装 `nvidia-container-toolkit`，或注释 docker-compose 中的 `deploy.resources` 块 |

## 许可证

Internal Use# PerRag
