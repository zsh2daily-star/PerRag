# API 接口文档

## 对话前端接入

### Open WebUI（已集成）

启动后访问 `http://localhost:3000`，首次使用需注册账号（数据存储在 `data/openwebui` 中）。Open WebUI 自动通过 `/v1/models` 发现可用模型，对话时内部走完整 RAG 链路。

### Hermes WebUI（外部连接）

Hermes WebUI 通过 `shared-rag` Docker 网络与 rag-api 通信。在 Hermes WebUI 中添加 OpenAI 兼容后端：

- **API Base URL**: `http://rag-api:8000/v1`
- **API Key**: 留空（RAG API 不强制鉴权）

确保 Hermes WebUI 容器已加入 `shared-rag` 网络：

```bash
docker network connect shared-rag hermes-webui
```

### CLI 命令行对话

如果只有终端环境，可以直接用 curl 调用 API 进行对话：

```bash
# 单次提问（非流式）
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "总结文档的主要内容"}' | jq -r '.answer'

# 流式输出（逐字打印，体验更好）
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "知识库里有哪些文件？"}],
    "stream": true
  }' --no-buffer

# 切换远程模型（DeepSeek）
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "文档中的核心观点是什么？"}],
    "stream": true
  }' --no-buffer
```

> **提示**：流式接口 `/v1/chat/completions` 支持 system prompt，可传 `{"role": "system", "content": "你是一个文档分析助手..."}` 来控制助手行为。

### CLI 文档导入

除了 REST API，还可以通过命令行直接导入文档（适合批量 / 脚本场景）：

```bash
# 进入容器执行
docker exec rag-api python -m app.import_docs --dir /app/data/uploads

# 或本地开发时直接运行
python -m app.import_docs --dir /path/to/docs
```

| 参数 | 说明 |
|------|------|
| `--dir` | 待导入目录路径（默认: `.env` 中的 `IMPORT_DIR`） |
| `--collection` | 目标 Qdrant collection，不传使用 `QDRANT_COLLECTION` 环境变量 |
| `--no-recursive` | 不递归扫描子目录（默认递归） |
| `--skip-existing` | 跳过已索引的文件，仅处理新增（增量导入） |
| `--replace` | 先删除已有同源数据再重新索引（默认追加模式） |

```bash
# 索引到指定 collection
docker exec rag-api python -m app.import_docs \
  --dir /app/data/uploads --collection my_custom_collection

# 增量导入（只处理新增文件，跳过已索引的）
docker exec rag-api python -m app.import_docs \
  --dir /app/data/uploads --skip-existing

# 强制重建索引（数据更新后重新导入）
docker exec rag-api python -m app.import_docs \
  --dir /app/data/uploads --replace

# 仅当前目录不递归
docker exec rag-api python -m app.import_docs \
  --dir /app/data/uploads --no-recursive
```

退出码：0 = 全部成功，1 = 有文件导入失败（方便 CI/脚本判断）。

---

## REST API 接口

> 以下示例假设服务运行在 `localhost:8000`，可通过 `curl http://localhost:8000/health` 确认服务状态。
>
> `jq` 用于格式化 JSON 输出，如未安装可去掉 `| jq` 或通过 `apt install jq` 安装。

### 端点总览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | API 基本信息 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/index/info` | 查看索引配置与状态 |
| `GET` | `/index/preview` | 预览待索引的文件列表 |
| `POST` | `/index/directory` | 同步索引指定目录 |
| `POST` | `/index/directory/async` | 异步索引（后台执行） |
| `POST` | `/list-docs` | 列出知识库中的文档 |
| `POST` | `/aggregate` | 跨文档全局统计/汇总 |
| `POST` | `/ask` | 核心问答接口（检索 + LLM 生成） |
| `GET` | `/v1/models` | OpenAI 兼容：列出可用模型 |
| `POST` | `/v1/chat/completions` | OpenAI 兼容：聊天补全（流式 SSE） |

---

### 基础接口

```bash
# 健康检查
curl http://localhost:8000/health
# → {"status":"ok"}

# 查看索引配置（嵌入模型、Qdrant 地址、支持的文件类型等）
curl -s http://localhost:8000/index/info | jq
# → {"import_dir":"/app/data/uploads","qdrant_host":"qdrant","qdrant_port":6333,...}

# 预览默认导入目录中的文件
curl -s http://localhost:8000/index/preview | jq
# → {"directory":"/app/data/uploads","total_files":5,"files":["/app/data/uploads/doc1.pdf",...]}

# 预览指定目录（支持子目录扫描）
curl -s "http://localhost:8000/index/preview?directory=/app/data/ragtemp&recursive=true" | jq

# 仅预览当前目录（不递归）
curl -s "http://localhost:8000/index/preview?recursive=false" | jq
```

---

### 索引管理

#### `POST /index/directory` — 同步索引

阻塞等待完成，适合少量文件。

```bash
# 基础用法：索引到默认 Collection
curl -s -X POST http://localhost:8000/index/directory \
  -H "Content-Type: application/json" \
  -d '{
    "directory": "/app/data/uploads",
    "recursive": true,
    "replace": false
  }' | jq

# 索引到指定 Collection（不传 collection 则用 .env 中的 QDRANT_COLLECTION）
curl -s -X POST http://localhost:8000/index/directory \
  -H "Content-Type: application/json" \
  -d '{
    "directory": "/app/data/uploads",
    "collection": "my_custom_collection",
    "recursive": true,
    "replace": false
  }' | jq
# → {"directory":"/app/data/uploads","collection":"my_custom_collection","total_files":3,"indexed":3,...}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `directory` | string | ✅ | 服务器上待索引的目录绝对路径 |
| `collection` | string | | 目标 Collection 名称，不传使用 `.env` 中 `QDRANT_COLLECTION` 的值 |
| `recursive` | bool | | 是否递归扫描子目录，默认 `true` |
| `replace` | bool | | 是否删除旧索引后重建，默认 `false`（追加） |

```bash
# 替换模式 —— 删除旧索引后重新导入
curl -s -X POST http://localhost:8000/index/directory \
  -H "Content-Type: application/json" \
  -d '{
    "directory": "/app/data/uploads",
    "recursive": true,
    "replace": true
  }' | jq
```

**响应字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `directory` | string | 索引的目录路径 |
| `total_files` | int | 扫描到的文件总数 |
| `indexed` | int | 成功索引数量 |
| `failed` | int | 失败数量 |
| `skipped` | int | 跳过数量（已存在且非 replace 模式） |
| `total_chunks` | int | 生成的总 chunk 数 |
| `collection` | string | 写入的 Qdrant collection 名称 |

#### `POST /index/directory/async` — 异步索引

立即返回，适合大目录。

```bash
# 异步索引到默认 Collection
curl -s -X POST http://localhost:8000/index/directory/async \
  -H "Content-Type: application/json" \
  -d '{
    "directory": "/app/data/uploads",
    "recursive": true,
    "replace": false
  }' | jq
# → {"message":"索引任务已在后台启动","directory":"/app/data/uploads","total_files":50}

# 异步索引到指定 Collection
curl -s -X POST http://localhost:8000/index/directory/async \
  -H "Content-Type: application/json" \
  -d '{
    "directory": "/app/data/uploads",
    "collection": "my_custom_collection",
    "recursive": true,
    "replace": false
  }' | jq
```

参数与同步索引一致。异步索引发起后可调 Agent 工具 `get_index_status` 查询进度。**注意**：异步模式目前仅限 Agent 工具触发，REST 接口不返回 `task_id`。

---

### 文档列表

#### `POST /list-docs` — 列出知识库文档

按文件名去重，返回每个文档的 chunk 数量。

```bash
# 列出默认 Collection 中的文档
curl -s -X POST http://localhost:8000/list-docs \
  -H "Content-Type: application/json" \
  -d '{}' | jq
# → {
#     "collection": "workFile",
#     "total_docs": 3,
#     "total_chunks": 42,
#     "documents": [
#       {"filename": "report.pdf", "source": "/app/data/uploads/report.pdf", "chunks": 15},
#       ...
#     ]
#   }

# 列出指定 Collection 中的文档
curl -s -X POST http://localhost:8000/list-docs \
  -H "Content-Type: application/json" \
  -d '{"collection": "rag_documents"}' | jq
```

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `collection` | string | | Collection 名称，不传使用默认值 |

**响应字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `collection` | string | Collection 名称 |
| `total_docs` | int | 文档总数（按文件名去重） |
| `total_chunks` | int | 总 chunk 数 |
| `documents` | array | 文档列表，每项含 `filename`、`source`、`chunks` |

---

### 文档聚合

#### `POST /aggregate` — 跨文档全局分析

多轮检索 → 去重 → LLM 全局统计分析。适合"总结知识库趋势"、"跨文档统计"类问题。

```bash
curl -s -X POST http://localhost:8000/aggregate \
  -H "Content-Type: application/json" \
  -d '{
    "query": "总结所有文档中关于机器学习的主要内容和发展趋势"
  }' | jq
# → {"query":"...","answer":"基于知识库中 3 份文档的分析...","sources":[...]}
```

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | ✅ | 分析主题或问题 |
| `filters` | object | | Metadata 过滤条件，如 `{"source_format": "pdf"}` |
| `collection` | string | | 单个 Collection 名称 |
| `collections` | array | | 多个 Collection 列表 |
| `llm_provider` | string | | 覆盖默认 LLM 提供方（`ollama` / `api`） |
| `llm_model` | string | | 覆盖默认模型名 |
| `llm_api_base` | string | | 覆盖默认 API 地址 |
| `llm_api_key` | string | | 覆盖默认 API 密钥 |

```bash
# 使用远程 API 做聚合（处理长上下文更稳定）
curl -s -X POST http://localhost:8000/aggregate \
  -H "Content-Type: application/json" \
  -d '{
    "query": "知识库中有哪些共同的统计数据和结论？",
    "llm_provider": "api",
    "llm_model": "deepseek-chat",
    "llm_api_key": "sk-your-key-here"
  }' | jq
```

---

### 问答

#### `POST /ask` — 核心问答

检索 + LLM 生成的完整 RAG 链路，支持请求级切换 LLM。

```bash
# 使用默认 Ollama 模型提问
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "知识库中有哪些关于机器学习的文档？"}' | jq
# → {"query":"...","answer":"根据知识库内容...","sources":[{...}]}
```

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | ✅ | 用户问题 |
| `collection` | string | | 目标知识库 Collection（如 `workfile` / `中医`），不传使用默认值 |
| `filters` | object | | Metadata 过滤条件，支持 `*` 通配符 |
| `llm_provider` | string | | `ollama` / `api` |
| `llm_model` | string | | 模型名（覆盖默认值） |
| `llm_api_base` | string | | API 地址（覆盖默认值） |
| `llm_api_key` | string | | API 密钥（覆盖默认值） |

**不同 LLM 后端示例**：

```bash
# ── 本地 Ollama ──────────────────────────
# 指定本地其他模型
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "query": "总结文档的主要内容",
    "llm_provider": "ollama",
    "llm_model": "qwen3:14b"
  }' | jq

# ── 远程 API（OpenAI 兼容）────────────────
# 使用 DeepSeek
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "query": "文档中提到的主要风险有哪些？",
    "llm_provider": "api",
    "llm_model": "deepseek-chat",
    "llm_api_key": "sk-your-key-here"
  }' | jq

# 使用自定义 API 地址（如 vLLM、Groq 等）
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "query": "帮我归纳文档的要点",
    "llm_provider": "api",
    "llm_model": "gpt-4o-mini",
    "llm_api_base": "https://api.openai.com/v1",
    "llm_api_key": "sk-your-openai-key"
  }' | jq
```

**路由器自动行为**：

```bash
# 路由器自动识别 list_docs 意图
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "知识库里有哪些文件？"}' | jq

# 路由器识别为闲聊，直接调 LLM（不走检索）
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "你好，请介绍一下你自己"}' | jq
```

**响应字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `query` | string | 原始问题 |
| `answer` | string | LLM 生成的回答 |
| `sources` | array | 参考文档列表，含 `text`、`source`、`filename`、`score`、`dense_rank`、`sparse_rank` |

---

### OpenAI 兼容端点

供 Open WebUI / Hermes WebUI 通过标准 OpenAI 协议接入。

#### `GET /v1/models` — 列出可用模型

自动发现 Ollama 本地模型 + API 远程模型。

```bash
curl -s http://localhost:8000/v1/models | jq
# → {
#     "object": "list",
#     "data": [
#     "data": [
#       {"id": "qwen3:8b", "object": "model", "owned_by": "ollama"},
#       {"id": "deepseek-chat", "object": "model", "owned_by": "api"},
#       {"id": "deepseek-chat", "object": "model", "owned_by": "api"},
#       ...
#     ]
#   }
```

#### `POST /v1/chat/completions` — 聊天补全

OpenAI 格式，支持流式 SSE。内部根据请求内容自动选择处理路径（详见 [ROUTING.md](ROUTING.md#v1chatcompletions-路由树)）。

```bash
# 基础用法 —— 与 ChatGPT API 格式一致
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [
      {"role": "system", "content": "你是一个文档分析助手，请基于知识库回答问题。"},
      {"role": "user", "content": "总结一下文档的核心观点"}
    ],
    "temperature": 0.3
  }' | jq

# 使用远程模型（模型名不在 Ollama 列表中时自动走 API）
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [
      {"role": "user", "content": "文档中提到的关键数据有哪些？"}
    ]
  }' | jq

# 多轮对话（自动使用最后一条 user 消息检索）
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [
      {"role": "user", "content": "知识库中有哪些报告？"},
      {"role": "assistant", "content": "知识库中有 3 份报告：A.pdf、B.docx、C.xlsx。"},
      {"role": "user", "content": "A 报告的主要内容是什么？"}
    ]
  }' | jq
```

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model` | string | | 模型名，不传使用默认 Ollama 模型 |
| `messages` | array | ✅ | 消息列表，标准 OpenAI 格式 |
| `stream` | bool | | 是否流式输出（SSE），默认 `false` |
| `tools` | array | | 工具列表（带 RAG 工具走 Agent 模式，其他走透传） |
| `temperature` | float | | 采样温度，默认 `0.3` |

**内部路由逻辑**：

所有请求自动经过 RAG Skill 预处理（`rag_skill.apply()`：system prompt 增补 + tools 补齐），然后进入统一 Hybrid Agent。LLM 自主决策，最多 6 轮。RAG 工具内部执行，外部工具外抛。

---

## Hybrid Agent 统一路由

`rag_skill.apply()` 自动完成 system prompt 增补 + tools 补齐后进入 Hybrid Agent 循环。LLM 自主决策：RAG 工具内部执行，外部工具外抛给调用方（如 Hermes 的 web_search、terminal），最多 6 轮。

### 文档列表缓存

启动时后台从 Qdrant scroll 全量构建文档列表缓存。list_documents 工具直接读内存，索引/删除操作后自动异步刷新。

### 常见问题覆盖

| 用户问题 | LLM 决策 |
|----------|----------|
| "知识库里有什么文件" | 调 list_documents → 读缓存 |
| "A 报告讲什么" | 调 search_knowledge_base → 检索 |
| "汇总所有合同金额" | 调 aggregate_documents → 多轮扩展 |
| 闲聊 / 常识 | 不调工具，直接回答 |
