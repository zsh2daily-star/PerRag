# 项目架构

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

## 核心特性

| 模块 | 说明 | 状态 |
|------|------|------|
| **文档解析** | 支持 PDF（MinerU/pypdf/auto）、Word、PPT、Excel、Markdown、TXT 等多种格式 | ✅ 已实现 |
| **向量化索引** | BAAI/bge-m3 嵌入 + Qdrant 向量数据库，Dense + Sparse 双向量，支持多 Collection 管理与批量索引 | ✅ 已实现 |
| **双路混合检索** | Dense（语义向量）+ Sparse（BGE-M3 词权重）双路召回，RRF 融合排序，LLM 自动指定目标 Collection | ✅ 已实现 |
| **重排优化** | bge-reranker-v2-m3 Cross-Encoder 对召回结果精排 | ✅ 已实现 |
| **双 LLM 后端** | 本地 Ollama + 远程 API（OpenAI 兼容，支持 DeepSeek/Groq 等），请求级切换 | ✅ 已实现 |
| **多 Collection 支持** | 知识库可分 Collection 管理，LLM 自动路由到正确的目标库 | ✅ 已实现 |
| **OCR 质量检测** | MinerU 解析后自动检测乱码，在 metadata 打 quality_warning 标记 | ✅ 已实现 |
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

## 项目结构

```
rag-project/
├── app/                          # 应用代码
│   ├── __init__.py               # 包初始化 & 模块速览
│   ├── main.py                   # FastAPI 入口 & API 端点（OpenAI 兼容、Hybrid Agent、流式 SSE）
│   ├── config.py                 # 全局配置（环境变量 → Settings 不可变数据类）
│   ├── models.py                 # 共享模型加载（Embedding / Sparse / Reranker 全局单例，GPU 自动检测）
│   ├── indexer.py                # 文档索引器（解析 → 分块 → Dense + Sparse 双向量写入 Qdrant）
│   ├── retriever.py              # 混合检索 & 问答（双路召回 → RRF 融合 → 重排 → LLM 生成 → 流式 → 多轮）
│   ├── parser.py                 # 文档解析器（PDF / Word / Excel / PPT / Markdown / TXT，自动编码检测）
│   ├── mineru_client.py          # MinerU PDF 解析 HTTP 客户端（GPU OCR）
│   ├── router.py                 # 查询路由器（启动时生成知识库概括 + 提问时智能分发 4 种意图）
│   ├── tools.py                  # Agent 工具注册表（注册表模式：定义+执行函数一体，10 个工具）
│   ├── skills.py                  # RAG Skill 预处理器（system prompt 增补 + tools 补齐，自动触发）
│   ├── conversation_store.py     # SQLite 对话历史持久化（多轮上下文跨重启保留）
│   └── import_docs.py            # CLI 文档导入工具（python -m app.import_docs）
├── docs/                         # 文档
│   ├── ARCHITECTURE.md           # 项目架构与模块说明（本文档）
│   ├── ROUTING.md                # 路由、检索流程与 Tool 透传详解
│   └── API.md                    # 接口文档（端点总览、参数说明、curl 示例、CLI 导入）
├── data/                         # 数据目录（挂载到容器）
│   ├── uploads/                  # 待导入文档（放入此处后通过 API 或 CLI 索引）
│   ├── mineru/                   # MinerU 解析临时文件
│   ├── qdrant/                   # Qdrant 持久化存储（向量数据）
│   └── openwebui/                # Open WebUI 用户数据（聊天记录、配置等）
├── models/                       # HuggingFace 模型缓存（HF_HOME，避免每次重启重下模型）
├── docker-compose.yml            # Docker Compose 编排（rag-api + qdrant + mineru + openwebui）
├── Dockerfile                    # 应用镜像构建（Python 3.12-slim）
├── requirements.txt              # Python 依赖
└── .env                          # 环境变量配置（docker compose 自动加载）
```

### 核心模块说明

| 模块 | 文件 | 职责 |
|------|------|------|
| **入口 & 端点** | `main.py` | FastAPI 路由注册、模型预热、OpenAI 兼容端点、流式 SSE、Hybrid Agent、Tool 透传 |
| **配置** | `config.py` | 不可变 Settings 数据类，所有环境变量集中管理 |
| **模型加载** | `models.py` | Embedding/Sparse/Reranker 三个模型的全局单例，GPU 自动检测，Ollama 模型发现 |
| **文档索引** | `indexer.py` | 解析 → 分块 → Dense/Sparse 双向量生成 → Qdrant 写入，支持异步批量索引 |
| **混合检索** | `retriever.py` | Dense+Sparse 双路召回 → RRF 融合 → Rerank → LLM 生成，含 HyDE、aggregate、多轮对话 |
| **文档解析** | `parser.py` | PDF(pypdf)/Word/Excel/PPT/Markdown/TXT 多格式解析，MinerU PDF 通过 HTTP 远程调用 |
| **MinerU 客户端** | `mineru_client.py` | MinerU GPU OCR HTTP 客户端 |
| **查询路由** | `router.py` | 启动时 LLM 生成知识库概括，提问时智能分发 chat/list_docs/aggregate/search |
| **Agent 工具** | `tools.py` | 注册表模式：10 个 tool（定义+执行函数一体），TOOLS/CORE_TOOLS/execute_tool 自动推导 |
| **RAG Skill** | `skills.py` | 请求预处理器：保留身份 + 追加 RAG 描述 + 补齐工具，所有请求自动触发 |
| **对话存储** | `conversation_store.py` | SQLite 持久化多轮对话历史 |
| **CLI 导入** | `import_docs.py` | 命令行批量文档导入工具 |

## 模型与硬件

### 模型清单

| 模型 | 用途 | 大小 | 硬件要求 |
|------|------|------|----------|
| `BAAI/bge-m3` | 嵌入向量（Dense）+ 词权重（Sparse） | ~2.2 GB | GPU 或 CPU |
| `BAAI/bge-reranker-v2-m3` | Cross-Encoder 重排精排 | ~2.2 GB | GPU（推荐）或 CPU |
| `qwen3:8b`（Ollama） | 对话生成 / 路由判断 | ~5.9 GB | GPU（推荐）或 CPU |
| `deepseek-chat`（远程 API） | 对话生成 / function calling | — | 无 |

> **显存参考**：BGE-M3 + Reranker 两个模型共约 3-4 GB 显存（fp16）。如使用本地 Ollama 模型（约 5.9 GB），推荐至少 **10 GB 显存**。远程 API 模式下无需本地 LLM 显存。

### 模型缓存

所有 HuggingFace 模型首次下载后缓存到 `./models/` 目录（挂载为容器内 `/app/models`），重启不需要重新下载。默认使用 HuggingFace 官方源，国内用户可在 `.env` 中添加：

```bash
HF_ENDPOINT=https://hf-mirror.com
```

模型目录结构：

```
models/
├── models--BAAI--bge-m3/           # Dense 嵌入 + Sparse 词权重模型 (~6.4 GB)
├── models--BAAI--bge-reranker-v2-m3/ # Cross-Encoder 重排模型 (~2.2 GB)
├── mineru-layout/                  # MinerU 版面分析模型 (~8.2 GB)
└── hub/                            # HuggingFace Hub 缓存
```

### 启动预热

容器启动后，模型在后台异步加载（约 10-20 秒），同时自动生成知识库概括。预热完成前，首次查询会自动阻塞等待。查看预热进度：

```bash
docker compose logs rag-api | grep "预热\|就绪"
```

预热日志示例：

```
加载嵌入模型: BAAI/bge-m3 (device=cuda)    → ✓ 嵌入模型已就绪 (~7s)
加载 BGE-M3 稀疏编码器                       → ✓ 稀疏编码器已就绪 (~2s)
加载重排模型: BAAI/bge-reranker-v2-m3       → ✓ 重排模型已就绪 (~1s)
模型预热完成                                  → 总计 ~10s
```
