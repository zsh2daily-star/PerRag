# RAG 文档问答系统

基于检索增强生成（RAG）的文档智能问答系统，支持 PDF/Word/PPT/Excel/Markdown 等多种格式文档的自动解析、向量化存储、混合检索与大模型问答。

> 📖 **详细文档**：
> - [项目架构与模块说明](docs/ARCHITECTURE.md) — 架构图、服务拓扑、项目结构、模型与硬件
> - [路由与检索详解](docs/ROUTING.md) — 路由树、检索流程、HyDE、Aggregate、Tool 透传、Hybrid Agent
> - [API 接口文档](docs/API.md) — 端点总览、参数说明、curl 示例、CLI 导入

## 架构概览

```
用户交互层     ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
              │  Open WebUI  │  │Hermes WebUI │  │  REST API    │  │  CLI 工具    │
              │  (聊天前端)   │  │ (聊天前端)   │  │  (FastAPI)   │  │  (import)   │
              └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
                     │                 │                 │                 │
应用服务层           └────────┬────────┴────────┬────────┘                 │
                     ┌────────┴────────┐                │                │
                     │  FastAPI 应用    │◄───────────────┴────────────────┘
                     │  ├─ Router      │  查询路由分发（chat/search/aggregate/list_docs）
                     │  ├─ Retriever   │  混合检索（Dense+Sparse→RRF→Rerank）+ LLM 生成
                     │  ├─ Indexer     │  文档索引（解析→分块→双向量写入 Qdrant）
                     │  └─ Parser      │  多格式解析（PDF/Word/Excel/PPT/Markdown/TXT）
                     └────────┬────────┘
                              │
AI 模型层      ┌──────────────┼──────────────┐
              │               │              │
         ┌────▼────┐   ┌─────▼──────┐  ┌────▼────────────┐
         │  Ollama │   │ BGE-M3     │  │ BGE-Reranker    │
                  │ Qwen3-8B │   │ (Embedding)│  │ (Cross-Encoder) │
         │  (宿主机) │   │   Dense +  │  │ 重排精排         │
         │         │   │   Sparse   │  │                 │
         └─────────┘   └────────────┘  └─────────────────┘
              │
数据存储层     ┌──────────────┐  ┌──────────────┐
              │   Qdrant     │  │  MinerU      │
              │  (向量数据库) │  │  (PDF解析)   │
              │  Dense+Sparse│  │  GPU OCR     │
              └──────────────┘  └──────────────┘
```

## 核心特性

| 模块 | 说明 | 状态 |
|------|------|------|
| **文档解析** | 支持 PDF（MinerU/pypdf/auto）、Word、PPT、Excel、Markdown、TXT 等多种格式 | ✅ 已实现 |
| **向量化索引** | BAAI/bge-m3 嵌入 + Qdrant 向量数据库，Dense + Sparse 双向量，支持批量索引与增量更新 | ✅ 已实现 |
| **双路混合检索** | Dense（语义向量）+ Sparse（BGE-M3 词权重）双路召回，RRF 融合排序 | ✅ 已实现 |
| **重排优化** | bge-reranker-v2-m3 Cross-Encoder 对召回结果精排 | ✅ 已实现 |
| **双 LLM 后端** | 本地 Ollama（默认 qwen3-xlam）+ 远程 API（OpenAI 兼容，支持 DeepSeek/Groq 等），请求级切换 | ✅ 已实现 |
| **查询路由** | 启动时自动生成知识库概括，提问时智能判断 chat/list_docs/aggregate/search 四种意图 | ✅ 已实现 |
| **Open WebUI 集成** | 通过 OpenAI 兼容端点 `/v1/chat/completions` + `/v1/models` 接入 Open WebUI | ✅ 已实现 |
| **文档列表查询** | 自然语言或 API 接口查询知识库中有哪些文件 | ✅ 已实现 |
| **跨文档聚合** | 多轮检索 + 汇总去重 + LLM 全局统计分析 | ✅ 已实现 |
| **Tool 透传 RAG 注入** | Hermes WebUI 请求自动注入知识库上下文，无需额外配置 | ✅ 已实现 |
| **Hybrid Agent 循环** | RAG 工具内部执行 + 外部工具透传 Hermes，最多 6 轮 | ✅ 已实现 |
| **文档列表缓存** | 启动时构建，内存读取零 QPS，索引变更自动刷新 | ✅ 已实现 |
| **扩展检索** | 检测"所有/汇总"关键词，自动多角度补充检索扩大召回 | ✅ 已实现 |
| **对话历史持久化** | SQLite 存储，跨重启保留多轮对话上下文 | ✅ 已实现 |
| **多轮对话 RAG** | 历史感知的检索查询构建 + 对话上下文传递给 LLM | ✅ 已实现 |

## 服务拓扑

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Open WebUI  │────▶│   rag-api    │────▶│   Qdrant     │
│  (端口:3000)  │     │  (核心:8000) │     │  (向量:6333) │
└──────────────┘     └──────┬───────┘     └──────────────┘
                            │  ▲
┌──────────────┐            │  │  OpenAI 兼容端点
│Hermes WebUI │────────────┘  │  /v1/models
│  (端口:6060)  │               │  /v1/chat/completions
└──────────────┘               │
              ┌─────────────┼─────────────┐
              │             │             │
        ┌─────▼─────┐ ┌────▼────┐ ┌──────▼──────┐
        │  MinerU   │ │ Ollama  │ │ API Remote  │
        │ (解析:30001)│ │(宿主机) │ │ (DeepSeek)  │
        └───────────┘ └─────────┘ └─────────────┘
```

> **说明**：Open WebUI 通过 Docker Compose 一同部署，默认端口 3000。Hermes WebUI 通过 `shared-rag` Docker 网络连接 rag-api 的 OpenAI 兼容端点。Ollama 运行在宿主机，不包含在 Docker 编排中。

## 快速开始

### 前置条件

- **Docker** & **Docker Compose** v2
- **Ollama** 运行在宿主机（Docker 外），已拉取模型：

```bash
# 宿主机上安装并启动 Ollama
ollama pull qwen3-xlam:latest

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
mkdir -p data/uploads data/mineru data/qdrant data/openwebui

# 3. 配置环境变量（可选，复制后按需修改）
cp .env .env.local

# 4. 构建并启动所有服务
docker compose up -d --build

# 5. 查看日志
docker compose logs -f rag-api

# 6. 健康检查
curl http://localhost:8000/health
```

### 导入文档

**方式一：API 接口**

```bash
# 将文档放到 data/uploads 目录
cp /path/to/your/docs/*.pdf data/uploads/

# 通过 API 触发同步索引
curl -X POST http://localhost:8000/index/directory \
  -H "Content-Type: application/json" \
  -d '{"directory": "/app/data/uploads", "recursive": true, "replace": false}'
```

**方式二：CLI 工具**

```bash
# 进入容器后使用 CLI 导入（更适合批量场景）
docker exec rag-api python -m app.import_docs --dir /app/data/uploads

# 索引到指定 collection
docker exec rag-api python -m app.import_docs \
  --dir /app/data/uploads --collection my_custom_collection

# 可选参数：
#   --collection     目标 Qdrant collection，不传使用 QDRANT_COLLECTION 环境变量
#   --no-recursive   不扫描子目录
#   --skip-existing  跳过已索引文件，仅导入新增（增量导入）
#   --replace        删除已有同源数据后重建（默认追加模式）
```

### 开始提问

```bash
# 使用本地 Ollama
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "知识库中有哪些关于机器学习的文档？"}'

# 切换到远程 DeepSeek
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "query": "总结文档的主要内容",
    "llm_provider": "api",
    "llm_model": "deepseek-chat",
    "llm_api_key": "sk-your-key-here"
  }'
```

## 对话前端与分析接口

三种使用方式：Open WebUI（已集成，端口 3000）、Hermes WebUI（外部连接）、CLI 命令行。

11 个 REST 端点：基础接口（health/info/preview）、索引管理（同步/异步）、文档列表、跨文档聚合、核心问答、OpenAI 兼容端点（models/chat completions）。

完整接口文档、参数说明和 curl 示例见 **[docs/API.md](docs/API.md)**。

## Tool 透传 RAG 增强

所有请求统一走 **Hybrid Agent 循环**：补齐 RAG 工具 + 保留原始 system prompt + LLM 自主决策。RAG 工具内部执行，外部工具（Hermes 的 web_search 等）外抛，最多 6 轮。

详见 **[docs/ROUTING.md](docs/ROUTING.md#v1chatcompletions-路由树)** 和 **[docs/API.md](docs/API.md#tool-透传-rag-增强hermes-webui-专用)**。

## 项目结构与模型

详见 **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**（架构图、服务拓扑、核心模块职责、模型清单、硬件要求、模型缓存、启动预热）。

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
| `OLLAMA_DEFAULT_MODEL` | `qwen3-xlam:latest` | 默认 Ollama 模型 |
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
| `RAG_API_PORT` | `8000` | RAG API 对外端口 |
| `OPENWEBUI_PORT` | `3000` | Open WebUI 对外端口 |
| `WEBUI_SECRET_KEY` | `change-me-in-production` | Open WebUI JWT 密钥 |

> **注意**：上表展示的是 Docker Compose 环境下的默认值。本地开发时，`app/config.py:119` 中的默认值有所不同（如 `QDRANT_HOST=localhost`、`OLLAMA_HOST=localhost`），Docker Compose 中的 `environment` 配置会覆盖这些默认值。

## 查询路由机制

系统启动时会自动分析 Qdrant 中所有 Collection 的内容，用 LLM 生成每个知识库的内容概括。每次提问时，路由器根据概括 + 用户问题判断该执行哪种行动：

| Action | 场景 | 处理方式 |
|--------|------|----------|
| `search` | 具体内容查询 | 双路混合检索 → 重排 → LLM 生成 |
| `aggregate` | 全局统计/汇总 | 跨文档聚合分析 |
| `list_docs` | 列出文档列表 | 从 Qdrant payload 提取文件名去重 |
| `chat` | 闲聊/常识 | 直接 LLM 回答，不使用知识库 |

## 检索架构

所有请求自动经过 **RAG Skill** 预处理（`rag_skill.apply()` 一行完成 system prompt 增补 + tools 补齐），然后进入**统一 Hybrid Agent**（LLM 自主决策：搜索/聚合/闲聊/外抛外部工具，最多 6 轮）。

完整检索链路：**HyDE（可选）→ Dense + Sparse（各 30 条）→ RRF → Rerank → Top 5 → LLM**。

详见 **[docs/ROUTING.md](docs/ROUTING.md)**。

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
| Open WebUI 不显示模型 | `/v1/models` 返回空 | 确认 Ollama 有已拉取的模型，检查 `rag-api` 日志 |
| 首次查询很慢 | 模型正在后台加载 | 查看 `docker compose logs rag-api` 等待预热完成（约 30-60s） |
| GPU 内存不足 | 同时加载 Embedding + Reranker 模型 | 两个模型共约 3-4GB 显存，确保 GPU 有足够可用内存 |
| 检索结果不准 | 文档未正确分块或索引 | 减小 `CHUNK_SIZE`、增大 `RERANK_TOP_K`，或检查文档解析质量 |
| Hermes WebUI 连接失败 | 容器不在同一网络 | `docker network connect shared-rag hermes-webui` |
| 流式输出乱码 | 终端编码问题 | 确保终端 UTF-8 编码，或使用非流式接口 |
| collection 不存在 | 尚未导入文档 | 先通过 `/index/directory` 导入文档，再提问 |
| 启动报错 "could not select device driver" | Docker 未配置 GPU | 安装 `nvidia-container-toolkit`，或注释 docker-compose 中的 `deploy.resources` 块 |

## 许可证

Internal Use# PerRag
