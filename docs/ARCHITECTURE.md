# 项目架构

## 架构概览

```
用户交互层     ┌──────────────┐  ┌──────────────┐
              │  Open WebUI  │  │Hermes WebUI │
              │  (聊天前端)   │  │ (聊天前端)   │
              └──────┬───────┘  └──────┬───────┘
                     │  直连 deepseek   │  直连 deepseek
                     │   + MCP 检索     │   + MCP 检索
                     └────────┬────────┘
                              │
MCP 检索服务     ┌────────────▼────────┐
              │       rag-mcp        │  MCP server（streamable-http）
              │  ├─ search/list/docs │  混合检索（Dense+Sparse→RRF→Rerank）
              │  ├─ get_content      │  文档内容查询
              │  ├─ index            │  文档索引（解析→分块→双向量写入 Qdrant）
              │  └─ delete/preview   │  删除 / 预览
              └────────┬────────┘
                       │
AI 模型层      ┌────────┼────────┐
              │                 │
         ┌────▼────┐   ┌───────▼─────┐  ┌─────────────────┐
         │ DeepSeek│   │   BGE-M3    │  │  BGE-Reranker   │
         │ (远程API)│   │ (Embedding) │  │ (Cross-Encoder) │
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
```

## 服务拓扑

```
┌──────────────┐     ┌──────────────┐
│  Open WebUI  │────▶│  DeepSeek    │
│  (端口:3000)  │     │ (api.deepseek)│
└──────┬───────┘     └──────────────┘
       │ MCP 检索
┌──────▼───────┐     ┌──────────────┐     ┌──────────────┐
│Hermes WebUI │────▶│   rag-mcp    │────▶│   Qdrant     │
│  (端口:6060) │     │  (MCP:8001)  │     │  (向量:6333) │
└──────────────┘     └──────┬───────┘     └──────────────┘
                            │ PDF 解析
                     ┌──────▼───────┐
                     │    MinerU    │
                     │  (端口:30001) │
                     └──────────────┘
```

> **说明**：
> - Open WebUI 与 Hermes WebUI 都直连 DeepSeek，检索通过 MCP 调用 rag-mcp（`rag-mcp:8001/mcp`）。
> - MinerU 提供 PDF OCR 解析。

## 核心特性

| 模块 | 说明 | 状态 |
|------|------|------|
| **文档解析** | 支持 PDF（MinerU/pypdf/auto）、Word、PPT、Excel、Markdown、TXT 等多种格式 | ✅ 已实现 |
| **向量化索引** | BAAI/bge-m3 嵌入 + Qdrant 向量数据库，Dense + Sparse 双向量，支持多 Collection 管理与批量索引 | ✅ 已实现 |
| **双路混合检索** | Dense（语义向量）+ Sparse（BGE-M3 词权重）双路召回，RRF 融合排序 | ✅ 已实现 |
| **重排优化** | bge-reranker-v2-m3 Cross-Encoder 对召回结果精排 | ✅ 已实现 |
| **多 Collection 支持** | 知识库可分 Collection 管理 | ✅ 已实现 |
| **OCR 质量检测** | MinerU 解析后自动检测乱码，在 metadata 打 quality_warning 标记 | ✅ 已实现 |
| **简单 RAG 问答** | `/v1/chat/completions` + `/ask` 走检索 + LLM 生成，无 agent 循环 | ✅ 已实现 |
| **MCP 检索服务** | 检索能力独立成 `rag-mcp` 服务（10 个 MCP 工具），供 Hermes 直连 deepseek 时统一编排 | ✅ 已实现 |
| **Open WebUI 集成** | 通过 OpenAI 兼容端点 `/v1/chat/completions` + `/v1/models` 接入 Open WebUI | ✅ 已实现 |
| **文档列表查询** | 自然语言或 API 接口查询知识库中有哪些文件 | ✅ 已实现 |
| **跨文档聚合** | 多轮检索 + 汇总去重 + LLM 全局统计分析 | ✅ 已实现 |
| **文档列表缓存** | 启动时构建，内存读取零 QPS，索引变更自动刷新 | ✅ 已实现 |

## 项目结构

```
rag-project/
├── app/                          # 应用代码
│   ├── __init__.py               # 包初始化 & 模块速览
│   ├── main.py                   # FastAPI 入口 & API 端点（OpenAI 兼容、简单 RAG）
│   ├── config.py                 # 全局配置（环境变量 → Settings 不可变数据类）
│   ├── models.py                 # 共享模型加载（Embedding / Sparse / Reranker 全局单例，GPU 自动检测）
│   ├── indexer.py                # 文档索引器（解析 → 分块 → Dense + Sparse 双向量写入 Qdrant）
│   ├── retriever.py              # 混合检索 & 问答（双路召回 → RRF 融合 → 重排 → LLM 生成）
│   ├── parser.py                 # 文档解析器（PDF / Word / Excel / PPT / Markdown / TXT，自动编码检测）
│   ├── mineru_client.py          # MinerU PDF 解析 HTTP 客户端（GPU OCR）
│   ├── tools.py                  # 工具注册表（10 个 handler，供 MCP 复用）
│   ├── mcp_server.py             # MCP 检索服务（把 10 个工具暴露为 MCP，供 Hermes 调用）
│   └── import_docs.py            # CLI 文档导入工具（python -m app.import_docs）
├── docs/                         # 文档
│   ├── ARCHITECTURE.md           # 项目架构与模块说明（本文档）
│   ├── ROUTING.md                # 路由与检索详解
│   └── API.md                    # 接口文档（端点总览、参数说明、curl 示例、CLI 导入）
├── data/                         # 数据目录（挂载到容器）
│   ├── uploads/                  # 待导入文档（放入此处后通过 API 或 CLI 索引）
│   ├── mineru/                   # MinerU 解析临时文件
│   ├── qdrant/                   # Qdrant 持久化存储（向量数据）
│   └── openwebui/                # Open WebUI 用户数据（聊天记录、配置等）
├── models/                       # HuggingFace 模型缓存（HF_HOME，避免每次重启重下模型）
├── docker-compose.yml            # Docker Compose 编排（rag-mcp + qdrant + mineru + openwebui）
├── Dockerfile                    # 应用镜像构建（Python 3.12-slim）
├── requirements.txt              # Python 依赖
└── .env                          # 环境变量配置（docker compose 自动加载）
```

### 核心模块说明

| 模块 | 文件 | 职责 |
|------|------|------|
| **入口 & 端点** | `main.py` | FastAPI 路由注册、模型预热、OpenAI 兼容端点、简单 RAG 问答 |
| **配置** | `config.py` | 不可变 Settings 数据类，所有环境变量集中管理 |
| **模型加载** | `models.py` | Embedding/Sparse/Reranker 三个模型的全局单例，GPU 自动检测，Ollama 模型发现 |
| **文档索引** | `indexer.py` | 解析 → 分块 → Dense/Sparse 双向量生成 → Qdrant 写入，支持异步批量索引 |
| **混合检索** | `retriever.py` | Dense+Sparse 双路召回 → RRF 融合 → Rerank → LLM 生成，含 HyDE、aggregate |
| **文档解析** | `parser.py` | PDF(pypdf)/Word/Excel/PPT/Markdown/TXT 多格式解析，MinerU PDF 通过 HTTP 远程调用 |
| **MinerU 客户端** | `mineru_client.py` | MinerU GPU OCR HTTP 客户端 |
| **工具注册表** | `tools.py` | 10 个 handler（定义+执行函数一体），供 MCP 直接复用 |
| **MCP 检索** | `mcp_server.py` | 把 10 个工具暴露为 MCP（streamable-http），供 Hermes 直连 deepseek 时调用 |
| **CLI 导入** | `import_docs.py` | 命令行批量文档导入工具 |

## 模型与硬件

### 模型清单

| 模型 | 用途 | 大小 | 硬件要求 |
|------|------|------|----------|
| `BAAI/bge-m3` | 嵌入向量（Dense）+ 词权重（Sparse） | ~2.2 GB | GPU 或 CPU |
| `BAAI/bge-reranker-v2-m3` | Cross-Encoder 重排精排 | ~2.2 GB | GPU（推荐）或 CPU |
| `qwen3:8b`（Ollama） | 对话生成 | ~5.9 GB | GPU（推荐）或 CPU |
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

容器启动后，模型在后台异步加载（约 10-20 秒），同时构建文档列表缓存。预热完成前，首次查询会自动阻塞等待。查看预热进度：

```bash
docker compose logs rag-mcp | grep "预热\|就绪"
```

预热日志示例：

```
加载嵌入模型: BAAI/bge-m3 (device=cuda)    → ✓ 嵌入模型已就绪 (~7s)
加载 BGE-M3 稀疏编码器                       → ✓ 稀疏编码器已就绪 (~2s)
加载重排模型: BAAI/bge-reranker-v2-m3       → ✓ 重排模型已就绪 (~1s)
模型预热完成                                  → 总计 ~10s
```
