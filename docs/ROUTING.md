# 路由、检索与 Tool 透传详解

## 查询路由机制（`/ask` 端点）

系统启动时会自动分析 Qdrant 中所有 Collection 的内容，用 LLM 生成每个知识库的内容概括。每次提问时，路由器根据概括 + 用户问题判断该执行哪种行动：

| Action | 场景 | 处理方式 |
|--------|------|----------|
| `search` | 具体内容查询 | 双路混合检索 → 重排 → LLM 生成 |
| `aggregate` | 全局统计/汇总 | 跨文档聚合分析 |
| `list_docs` | 列出文档列表 | 从 Qdrant payload 提取文件名去重 |
| `chat` | 闲聊/常识 | 直接 LLM 回答，不使用知识库 |

---

## `/v1/chat/completions` 路由树

所有请求自动经过 RAG Skill 预处理后，统一走 Hybrid Agent。

```
POST /v1/chat/completions
│
│  ┌─ RAG Skill（自动触发）─────────────────────────────┐
│  │  rag_skill.apply(messages, tools)                             │
│  │    1. _augment_system_prompt() — 保留原始身份 + 追加 RAG 描述   │
│  │    2. _ensure_rag_tools()     — 补齐 search/aggregate/list     │
│  └─────────────────────────────────────────────────────────────┘
│
└─ 【统一 Hybrid Agent 循环】最多 6 轮
    LLM 自主决策:
      - 闲聊              → 直接回答，不调工具
      - 查文档             → search_knowledge_base  → 内部检索
      - 汇总统计           → aggregate_documents   → 内部聚合
      - 列出文件           → list_documents        → 读缓存
      - 外部工具 (Hermes)  → 外抛 tool_calls
    RAG 工具 rag-api 内部执行，外部工具透传给 Hermes
```

> **LLM 后端**：系统支持双后端：本地 Ollama（默认 `qwen3:8b`，用于轻量路由判断）和远程 DeepSeek API（默认 `deepseek-chat`，用于 function calling 和 RAG 问答）。远程模型支持 tool_calls，是 Agent 模式下推荐的后端。模型可通过请求 body 中的 `model` 字段指定，不在 Ollama 列表中的模型名自动走远程 API。

### 路由演进

| 版本 | 逻辑 |
|------|------|
| v1 | 三条分支：Agent / Tool 透传(ollama注入 vs api Hybrid) / 纯RAG |
| v2（上一步） | ollama 上下文注入 + api Hybrid Agent，通过 `route_query` 判断意图 |
| v3 | 统一 Hybrid Agent，换 xLAM 模型后 ollama 也走 Agent 循环 |
| v4（当前） | 注册表模式（tools.py）+ RAG Skill 预处理器（skills.py），一行 apply() 完成预处理，支持多 Collection 路由 |

---

## 检索架构详解

### 完整检索链路

```
用户提问（或对话历史构建的检索 query）
  │
  ├─ [可选] HyDE 查询扩展 ─────────────────────────────────────┐
  │   条件: HYDE_ENABLED=true                                   │
  │   原理: LLM 生成"假设答案"代替原始 query 做向量检索          │
  │   例如: "什么项目好?" → "市场前景好、技术可行、团队强…"      │
  │   开销: +1 次 LLM 调用（约 1-3 秒）                        │
  │   注意: Agent 模式下额外开销累积，默认关闭                   │
  │                                                             │
  ├─ 1. Dense 检索（语义匹配）───────────────────────────────── │
  │   BGE-M3 稠密向量 → Qdrant dense 集合                       │
  │   相似度搜索: "人工智能" 能匹配到 "机器学习"                 │
  │   召回数量: RETRIEVAL_TOP_K = 30 条                         │
  │                                                             │
  ├─ 2. Sparse 检索（关键词匹配）────────────────────────────── │
  │   BGE-M3 词权重 → Qdrant sparse 集合                        │
  │   精确匹配: 区分 "苹果公司" 和 "水果苹果"                    │
  │   召回数量: RETRIEVAL_TOP_K = 30 条                         │
  │   降级策略: 稀疏集合不存在时自动跳过，仅用 Dense             │
  │                                                             │
  ├─ 3. RRF 融合（倒数秩融合）───────────────────────────────── │
  │   公式: score(doc) = Σ 1/(k + rank_i), k=60                 │
  │   Dense 权重: RRF_DENSE_WEIGHT = 1.0                        │
  │   Sparse 权重: RRF_SPARSE_WEIGHT = 1.0                      │
  │   两路命中同一文档时分数累加                                 │
  │   去重策略: text[:200] 相同 → 保留排名更高的                 │
  │   理论上限: 60 条（两路完全不重叠时）                        │
  │                                                             │
  ├─ 4. Cross-Encoder 重排（精排）───────────────────────────── │
  │   BGE-Reranker-v2-M3 对 RRF 结果逐条精细对比                 │
  │   Bi-Encoder(快)粗筛 → Cross-Encoder(慢但准)精排             │
  │   query+doc 拼接编码: 能捕捉 "not" 等否定词的语义翻转        │
  │   分批处理: RERANK_BATCH_SIZE = 16 对/批（避免 OOM）         │
  │   最终输出: RERANK_TOP_K = 5 条                             │
  │                                                             │
  └─ 5. LLM 生成（回答）─────────────────────────────────────── │
      拼接上下文 + 问题 → Ollama/远程 API 生成最终回答           │
      上下文中的每段文档以 [N] 编号，LLM 引用时标注来源 [1][2]   │
```

### 为什么需要双路检索？

- **Dense（稠密向量）**：语义相似度搜索，能理解"人工智能"和"机器学习"是相关概念。召回率高但精确率较低。
- **Sparse（稀疏词权重）**：精确的关键词匹配，能区分"苹果公司"和"水果苹果"。精确率高但召回面窄。
- **两者互补**：融合后综合最优。

### 为什么需要 Cross-Encoder 重排？

- **Bi-Encoder（BGE-M3 编码阶段）**：分离编码 query 和 doc，速度快但交互不深
- **Cross-Encoder（Reranker 重排阶段）**：把 query+doc 拼在一起编码，精细对比但慢
- **策略**：Bi-Encoder 粗筛 30 条，Cross-Encoder 细排 Top 10 → 5，兼顾速度与质量

### Dense 与 Sparse 的融合比例

两路检索**等量召回**（各 30 条，1:1），但可以通过 RRF 权重调整影响力：

| 参数 | 默认值 | 作用 |
|------|--------|------|
| `RETRIEVAL_TOP_K` | `30` | 每路分别召回的数量 |
| `RRF_DENSE_WEIGHT` | `1.0` | Dense 路在 RRF 中的权重系数 |
| `RRF_SPARSE_WEIGHT` | `1.0` | Sparse 路在 RRF 中的权重系数 |
| `RERANK_TOP_K` | `5` | 重排后最终返回给 LLM 的数量 |

调整示例：

```bash
# 偏重语义匹配（降低关键词权重）
RRF_DENSE_WEIGHT=1.0
RRF_SPARSE_WEIGHT=0.7

# 偏重关键词精确匹配（降低语义权重）
RRF_DENSE_WEIGHT=0.6
RRF_SPARSE_WEIGHT=1.0

# 扩大 LLM 可用的上下文窗口
RERANK_TOP_K=10
```

---

## HyDE（假设文档嵌入）

**原理**：用户提问往往简短抽象（"什么项目算好项目？"），直接做向量检索效果差——因为知识库里的文档不会用这种提问句式来写。HyDE 让 LLM 先生成一段"假设答案"，包含具体术语和细节描述，再用这段假设答案做检索。

```
原始 query: "什么样的项目算好项目？"
      │
      ▼
LLM 生成假设答案: "具备市场前景良好、技术方案可行、团队经验丰富、商业模式清晰、
                 风险可控等特征的项目可以被认为是好项目。"
      │
      ▼
用假设答案做 Dense + Sparse 检索 → 匹配到更多相关文档
```

**启用方式**：`.env` 中设置 `HYDE_ENABLED=true`（默认关闭）。

**生效范围**：所有经过 `retrieve()` 的调用——`/ask`、`/aggregate`、`/v1/chat/completions`、Agent 模式——自动生效。

**开销**：每次检索额外增加 1 次 LLM 调用（约 1-3 秒）。在 Agent 循环中因 LLM 已经多次调用，建议保持关闭。

**降级策略**：LLM 调用失败时自动回退为原始 query，不影响检索可用性。

---

## Aggregate 跨文档聚合流程

`/aggregate` 接口专为**全局汇总类**问题设计（如"知识库中关于 A 公司的所有信息"）。与 `/ask` 的精简回答不同，aggregate 追求覆盖面。

```
用户提问: "汇总知识库中关于A公司的所有信息"
  │
  ├── 第一轮：扩大检索 ────────────────────────────────────────
  │   对每个目标 collection:
  │     retrieve(query, top_k=AGGREGATE_TOP_K)  ← 召回 30 条（而非默认 5 条）
  │     完整链路: Dense + Sparse → RRF → Rerank（保留 rerank_score）
  │   跨 collection 合并，按 text[:200] 去重
  │
  ├── 第二轮：多角度扩展检索 ──────────────────────────────────
  │   扩展 query 生成:
  │     "总结 {原始query}"
  │     "统计 {原始query}"
  │     "{原始query} 的发展趋势"
  │   对前 2 个 collection × 前 2 个扩展 query 做补充检索
  │     retrieve(扩展query, top_k=20)  ← 每次 20 条
  │   同样按 text[:200] 去重（与第一轮结果合并去重）
  │
  ├── 排序 & 截断 ────────────────────────────────────────────
  │   按 rerank_score 降序排列（相关度高的优先）
  │   截取 Top 30 条送 LLM 分析
  │
  └── LLM 全局分析 ───────────────────────────────────────────
      专用提示词:
        "请优先采信相关度高的文档片段"
        "尽量涵盖多个文档来源"
        "如有统计数据或趋势信息，请一并汇总"
      返回: answer + 带 rerank_score 的 sources 列表
```

与 `/ask` 的核心区别：

| | `/ask` | `/aggregate` |
|---|---|---|
| 召回量 | 每路 30 → RRF → Rerank → **Top 5** | 每路 30 → RRF → Rerank → **保留全部** |
| 检索轮次 | 1 轮 | 2 轮（主检索 + 多角度扩展，最多 5 次检索） |
| 去重范围 | RRF 融合时去重 | 跨轮次 + 跨 collection 去重 |
| LLM 策略 | 基于文档精确回答 | 全局汇总分析 + 多源覆盖 |
| 相关度排序 | 最终返回前 5 条 | 全部标注 rerank_score 供 LLM 采信 |

---

## Hybrid Agent 循环（统一入口）

所有请求经过 RAG Skill 预处理后进入 Hybrid Agent 循环。LLM 收到完整 tools 列表（RAG 工具 + 原始外部工具）后自主决策，最多 6 轮。

### 工具分类策略

```
LLM 返回 tool_calls
  │
  ├─ 全部是 RAG 工具（search / aggregate / list / delete 等）
  │   → rag-api 内部执行 → 结果追加到 conversation → 继续 LLM 循环
  │
  ├─ 混合 RAG + 外部工具（web_search / terminal / read_file / ...）
  │   → 包装所有 tool_calls → 返回给调用方（如 Hermes）→ 循环结束
  │
  └─ 无 tool_calls → 最终回答，循环结束

最多 6 轮，每轮约 3 秒（纯检索，不调 LLM 生成）
```

### 文档列表缓存

启动时后台从 Qdrant scroll 全量构建文档列表缓存。list_documents 工具直接读内存，索引变更后自动异步刷新。

### 常见问题覆盖

| 用户问题 | LLM 决策 |
|----------|----------|
| "知识库里有什么文件" | 调 list_documents → 读缓存返回 |
| "A 报告讲什么" | 调 search_knowledge_base → 检索 → 生成 |
| "汇总所有合同金额" | 调 aggregate_documents → 多轮扩展检索 |
| "删掉这个文件" | 调 delete_document → 删除索引 |
| 闲聊 / 常识 | 不调任何工具，直接回答 |

---

## 检索参数速查

```
                                        最终给 LLM 的文档数
                                        │
  RETRIEVAL_TOP_K=30  ──→  RRF  ──→  RERANK_TOP_K=5
       │                           │
  每路召回 30 条              Cross-Encoder 精排后截断
  Dense + Sparse
  （共 60 条进入融合）
```

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

---

## 多 Collection 路由

系统支持多个知识库 Collection（如 `workfile` 存放公司文件、`中医` 存放古籍文献）。LLM 在每次请求时会通过 system prompt 获取可用的 Collection 列表。

```
请求进入
  ├─ RAG Skill 预处理
  │   └─ _get_collections_hint() → 从 Qdrant 实时查询可用 Collection
  │   └─ 注入到 system prompt: "当前可用的知识库集合: workfile, 中医, rag_documents"
  │
  └─ LLM 决策
      ├─ "五运六气是什么" → search_knowledge_base(query="五运六气", collection="中医")
      ├─ "佛吉亚公司简介" → search_knowledge_base(query="佛吉亚", collection="workfile")
      └─ "列出中医知识库的文件" → list_documents(collection="中医")
```

所有 RAG 核心工具（search_knowledge_base、aggregate_documents、list_documents、delete_document、get_document_content）均支持 `collection` 参数，不传时使用 `.env` 中 `QDRANT_COLLECTION` 的默认值。

---

## OCR 解析质量检测

MinerU 解析 PDF 后自动运行 `check_parse_quality()` 检测输出质量。算法基于孤立字符密度分析，区分 OCR 乱码与正常文档中的数字/英文缩写。

检测结果（`score`、`is_garbled`、`details`）记录在日志中。若检测到乱码，文档的 `quality_warning` 字段会写入 Qdrant payload 的 metadata，检索时 LLM 可见此标记。
