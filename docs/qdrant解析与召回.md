# Qdrant 解析与召回

> 适用状态：2026-10-04。核心代码在 [app/core_Qdrant/](../app/core_Qdrant/)，检索/索引能力通过 rag-mcp 的 11 个知识库工具暴露给 Hermes（工具清单见 [MCP 工具调用](mcp工具调用.md)）。

## 1. 链路总览

```
文件（本地路径 / data/uploads / 只读挂载的公司文件）
  │  parser.py 统一解析（PDF 默认走 MinerU，其余走对应库）
  ▼
ParsedDocument（markdown 纯文本 + page_spans 页码区间 + metadata）
  │  LlamaIndex SentenceSplitter（512/50，可配语义切分）
  ▼
TextNode 切片（补 page_number / section_title / material_id 元数据）
  ├─► BGE-M3 稠密向量 ──► Qdrant 集合 {collection}（向量名 text-dense，COSINE）
  └─► BGE-M3 稀疏词权重 ─► Qdrant 集合 {collection}_sparse（向量名 text）

查询：query → 稠密路 top30 + 稀疏路 top30 → RRF 融合(k=60) → bge-reranker-v2-m3 重排 → top N
```

- 检索与索引**在 rag-mcp 进程执行**（Agent 调用工具时模型懒加载）；rag-api 进程也加载同一套模型（启动后台预热），用于材料索引任务和 `/evidence` 证据接口。
- HyDE（LLM 生成假设答案扩查）有代码但默认关闭（`HYDE_ENABLED=false`）。
- 不设分数阈值，始终返回 top_k 条。

## 2. 解析层

### 2.1 MinerU PDF 客户端（[mineru_client.py](../app/core_Qdrant/mineru_client.py)）

- 调 MinerU 容器 `POST http://mineru:8000/pdf_parse`（multipart 上传 PDF，`parse_method=auto`），超时默认 600s，取返回 JSON 的 `md_content`。
- 返回 `MinerUResult{markdown, page_spans}`：page_spans 记录每段文本在全文中的字符区间与起始页（0-based）。
- **大 PDF 分片**：pypdf 读页数，超过 `MINERU_PAGE_CHUNK_SIZE`（默认 20 页）切成临时 PDF 入 FIFO 队列逐片解析；每片解析前把嵌入/重排模型 offload 到 CPU 腾显存；失败重启 MinerU 重试一次，再失败折半分片（下限 5 页）；合并时按页序拼接并生成 page_spans。
- 支持手动/自动降级：`PDF_PARSER=mineru|pypdf|auto`。auto 时 MinerU 失败降级 pypdf 纯文本抽取（无文本层则报错）。
- OCR 乱码检测 `check_parse_quality()`：按中文文本中孤立数字/拉丁/空格密度给解析质量打分，异常时在 metadata 写 `quality_warning`，不阻断索引。
- 注：客户端内"重启 MinerU 容器"依赖 docker.sock，compose 未挂载，实际走静默降级分支。

### 2.2 统一解析器（[parser.py](../app/core_Qdrant/parser.py)）

| 格式 | 方式 |
|---|---|
| .pdf | MinerU（默认）/ pypdf 降级 |
| .doc | antiword → python-docx → olefile 三级降级 |
| .docx | python-docx 段落 + 表格，文档属性入 metadata |
| .xlsx/.xls | openpyxl，每个 sheet 加 `--- sheet名 ---` 头，metadata 记 sheet_count |
| .pptx/.ppt | python-pptx，含表格，metadata 记 slide_count |
| .md/.markdown | 直读，支持简易 YAML frontmatter |
| .txt | chardet 探测编码（兼容 gb18030 等） |

输出 `ParsedDocument{text, metadata, page_count, source_format, pages}`。章节定位：`extract_sections()` 扫描 markdown `#{1,6}` 标题行给出标题管辖的字符区间；`locate_page()` / `locate_section()` 按字符偏移反查页码与章节。

## 3. 切片与元数据（[indexer.py](../app/core_Qdrant/indexer.py)）

- 切片器：LlamaIndex `SentenceSplitter`，`chunk_size=512`、`chunk_overlap=50`（token）；`chunk_method=semantic` 时按段落 + 中文句读二级切分。
- 每个切片节点写入的 payload 元数据：
  - `source`（绝对路径）、`filename`、`source_format`、`material_id`（业务材料 uid，缺省时 `sha1(source)[:16]`）
  - `page_number`（按 node.start_char_idx 在 page_spans 中定位，非分片 PDF 为 null）
  - `section_title`（按偏移定位的 markdown 标题）
  - 图片节点（默认关闭，`MULTIMODAL_ENABLED=true` 且 PDF 时生效）额外带 image_path/image_index。
- LlamaIndex 节点自带 `_node_content`（含切片原文）。

## 4. 索引写入

### 4.1 双集合设计

| 物理集合 | 向量 | 模型 | 距离 | payload |
|---|---|---|---|---|
| `{collection}` | `text-dense` | BGE-M3 稠密（1024 维，维度动态探测建集） | COSINE | LlamaIndex 格式：元数据 + `_node_content` |
| `{collection}_sparse` | `text` | BGE-M3 BGEM3FlagModel 稀疏 lexical_weights | — | 显式字段：source / filename / text / chunk_index / page_number / section_title / material_id |

集合名由 `QDRANT_COLLECTION` 决定（代码默认 rag_documents，.env 模板为 workFile），首次写入时按需创建；每个逻辑库对应一对物理集合。同 filename 重复索引时先删旧向量（dense + sparse 两路）。

### 4.2 三个索引入口

1. **MCP 单文件**：`qdrant_file_index`（[handlers.py](../app/mcp/tool_Qdrant/handlers.py)）→ `index_file()`。内置宿主机路径映射：`/mnt/个人文件 → /app/data/personal`、`/mnt/公司文件/公司内部文件 → /app/data/ragtemp`，支持排除目录与查重。
2. **MCP 目录**：`qdrant_directory_index` → 递归收集文件批量索引；async_mode 返回 task_id，进度存进程内存字典（容器重启丢失），`qdrant_index_status_get` 查询。
3. **材料上传（rag-api）**：材料登记后 BackgroundTasks 调 material_service 索引，状态 pending→indexing→indexed/failed；完成后用 `set_payload` 给 dense 集合补写范围字段 `material_uid / enterprise_codes / scope_industry / scope_region`（稀疏集合不补）。

另有 CLI：`python -m app.import_docs --dir <目录>`。

## 5. 召回（[retriever.py](../app/core_Qdrant/retriever.py)）

`Retriever.retrieve(query, filters, top_k)` 步骤：

1. **过滤条件**：filters dict 编译为 Qdrant `must`（AND）；值含 `*` 时去掉星号走 MatchText 全文匹配。
2. **稠密检索** top30：无过滤走 LlamaIndex 快路径（返回字段较少）；有过滤直连 qdrant-client `query_points(using="text-dense")`，返回带页码/章节/material_id。
3. **稀疏检索** top30：BGEM3FlagModel 对 query 编码取 lexical_weights，`query_points(using="text")`；稀疏集合不存在或出错时静默降级为仅稠密。
4. **RRF 融合**：`k=60`，按正文前 200 字符去重；dense/sparse 权重默认均 1.0，输出带 dense_rank / sparse_rank / rrf_score。
5. **重排**：`bge-reranker-v2-m3` 对 [query, 切片] 对打分（批大小 16），按 rerank_score 降序。
6. 截断返回（默认 top5；MCP 检索工具固定取 top10，聚合工具取 top30）。

**聚合检索** `aggregate_docs()`：用 `[原 query, "汇总 "+query]` 两个查询变体 × 多个集合矩阵扩检，前缀去重后按重排分排序取前 30；不调 LLM，归纳分析交给 Hermes。

每条返回：text / source / filename / score / rrf_score / 各名次 / rerank_score，过滤路径下另带 page_number / section_title / material_id。

## 6. 文档元数据与证据（[docs_meta.py](../app/core_Qdrant/docs_meta.py)）

短连接 QdrantClient 的只读视图（带缓存，rag-api 启动预热，索引/删除后刷新）：

- `docs_meta()`：scroll 全量按 filename 去重 → 文件名 / 来源 / chunk 数（上限 5 万点）。
- `document_meta()`：单文档 chunk 数与来源路径（删除前核实）。
- `get_document_chunks()`：以 filename 自身做 query embedding + 精确过滤，按序取切片原文。
- `list_collections()`：列集合（排除 `_sparse` 后缀）。
- `delete_document()`：联动删除 dense + sparse 两路向量。
- `get_evidence(material_id, page_number?)`：按 material_id scroll dense 集合、按 chunk_index 排序返回原文，供 rag-api `/evidence/{material_id}` 佐证溯源（前端材料页"证据分片"）。

## 7. 模型与显存调度（[models.py](../app/core_Qdrant/models.py)）

| 模型 | 用途 | 来源 |
|---|---|---|
| BAAI/bge-m3 | 稠密嵌入 + 稀疏词权重（两个加载形式：HuggingFaceEmbedding / BGEM3FlagModel） | HF 缓存 `models/`（容器内 /app/models） |
| BAAI/bge-reranker-v2-m3 | Cross-Encoder 重排（GPU 下 fp16） | 同上 |
| MinerU/PaddleOCR 权重 | PDF 解析 | models/mineru（MinerU 容器 /opt/models） |

- 三个模型均为进程内 double-checked-locking 单例；rag-api 与 rag-mcp **各加载一份**，设备由 `EMBED_DEVICE` 决定（compose 默认 cpu，有 GPU 时自动 cuda）。
- MinerU 解析前 `offload_models_to_cpu()`（.to('cpu') + gc + empty_cache），后续调用自动回迁，避免与解析抢显存。
- LLM 调用（`_call_llm` 等）仅服务于 HyDE；答案生成在 Hermes 进程，不在本模块。

## 8. 相关环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| QDRANT_COLLECTION | rag_documents（.env 模板 workFile） | 默认集合名 |
| CHUNK_SIZE / CHUNK_OVERLAP | 512 / 50 | 切片大小与重叠 |
| CHUNK_METHOD | fixed | fixed / semantic |
| PDF_PARSER | mineru | mineru / pypdf / auto |
| MINERU_TIMEOUT | 600 | 解析超时秒数 |
| MINERU_PAGE_CHUNK_SIZE / MIN_CHUNK_SIZE / CHUNK_RESTART_EVERY | 20 / 5 / 5 | 大 PDF 分片策略 |
| RETRIEVAL_TOP_K | 30 | 每路召回数 |
| RERANK_TOP_K / RERANK_BATCH_SIZE | 5 / 16 | 重排返回数与批大小 |
| RRF_DENSE_WEIGHT / RRF_SPARSE_WEIGHT | 1.0 / 1.0 | RRF 双路权重 |
| HYDE_ENABLED | false | 假设答案扩查开关 |
| MULTIMODAL_ENABLED | false | 图片切片开关 |
| EMBEDDING_MODEL / RERANKER_MODEL / EMBED_DEVICE | bge-m3 / bge-reranker-v2-m3 / cpu | 模型与设备 |
