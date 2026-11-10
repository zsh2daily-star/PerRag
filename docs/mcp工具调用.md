# MCP 工具调用

> 适用状态：2026-10-04。rag-mcp 进程基于官方 MCP Python SDK 的 **FastMCP**，以 streamable-http 暴露 30 个工具，供 Hermes Agent 在对话中调用。
> 代码：[app/mcp/](../app/mcp/)。知识库工具背后的解析/检索原理见 [Qdrant 解析与召回](qdrant解析与召回.md)，业务库表见 [SQL 表结构](sql表结构.md)。

## 1. 服务形态

- 入口：[server.py](../app/mcp/server.py)，`FastMCP("rag-search")`，compose 启动命令 `python -m app.mcp.server`。
- 传输：`mcp.run(transport="streamable-http")`，监听 `0.0.0.0:8001`，路径 **`/mcp`**（即 `http://rag-mcp:8001/mcp`）；已关闭 DNS rebinding 保护，允许容器名访问。
- 注册：server.py 调三个 register 函数，工具全部用闭包内 `@mcp.tool()` 注册（函数 docstring 即工具描述，LLM 据此选择工具）。
- 与 rag-api 是**同镜像、两个独立进程**：rag-mcp 不经过 FastAPI 中间件，直连 PostgreSQL（asyncpg）与 Qdrant。

Hermes 侧注册（`/home/zshay/HermesDocker/.hermes/config.yaml`）：

```yaml
mcp_servers:
  rag-search:
    url: http://rag-mcp:8001/mcp
    enabled: true
```

无 headers/Authorization：**MCP 端点本身无鉴权**，安全边界是 Docker 网络（8001 不发布到宿主，只有 shared-rag 网络内的 Hermes 可达）。

## 2. 工具全景（30 个，4 组）

| 组 | 文件 | 数量 | 返回格式 | 读写边界 |
|---|---|---|---|---|
| 台账只读 | [tool_Sql/business.py](../app/mcp/tool_Sql/business.py) | 6 | JSON | 只读 |
| Excel 逐行导入 | [tool_mix/excel_import.py](../app/mcp/tool_mix/excel_import.py) | 8 | JSON | commit/flag 可写库（默认 dry_run） |
| 企业补全 | [tool_mix/backfill.py](../app/mcp/tool_mix/backfill.py) | 5 | JSON | backfill/commit 可写库（默认 dry_run） |
| 知识库 | [tool_Qdrant/tools.py](../app/mcp/tool_Qdrant/tools.py) + handlers.py | 11 | 纯文本 | index/delete 可写 Qdrant（有开关） |

## 3. 台账只读组（6 个）

| 工具 | 关键入参 | 返回 / 说明 |
|---|---|---|
| sql_enterprises_query | keyword, industry, region, limit=20 | `{total, enterprises[{id,name,code,industry,region,address,legal_person}]}` |
| sql_inspection_records_query | status, enterprise_keyword, hazard_level, keyword, limit=20 | 隐患明细分页；企业关键词命中多家时返回 `{ambiguous:true, hint, candidates[]}` 强制消歧（见第 6 节） |
| sql_inspection_stats_get | — | 按状态/等级计数 + 超期数 |
| sql_inspection_overdue_list | limit=10 | 超期督办清单（按截止日升序） |
| sql_materials_query | keyword, status, limit=20 | 材料登记元信息（含范围、上传人） |
| sql_dashboard_summary_get | — | 工作台一屏总览：未闭环/超期/本月新增/材料总数 + 等级分布 + 超期 Top5 + 最近材料 Top5 |

枚举：status = open/rectifying/closed；hazard_level = low/medium/high；material.status = pending/indexing/indexed/failed。

## 4. Excel 逐行导入组（8 个）

所有 Excel 工具的入参是**服务器本地绝对路径**（默认 `data/uploads/各类企业情况汇总（2026）.xlsx`），没有文件上传环节，也不使用 SQL 临时表；解析中间结果只在进程内存。

标准链路（table ∈ enterprise / inspection / material）：

```
excel_sheets_list          ① 列出 Sheet
excel_workbook_plan        ② 全簿体检：每个 Sheet 的列映射建议 + 置信度 + 跳过/人工复核建议（推荐入口）
excel_sheet_inspect        ② 单 Sheet 体检：表头、样例、建议列映射、无表头时按列序号映射
excel_sheet_parse          ③ 解析+规范化+按身份键聚合（不碰库）
sql_record_preview         ④ 单条 diff 预演：action=created/updated/skipped/failed + 字段变化（不写库）
sql_record_commit          ⑤ 确认后落库，返回批次聚合报告（可新建记录）
sql_record_flag            单 bool/枚举字段批量打标（dry_run 默认 true）
import_report_get          ⑥ 查批次报告（内存保留最近 20 批，重启清空）
```

批次报告含：batch_id（`CMT-时间戳-随机` / `FLG-...`）、counts{created,updated,skipped,failed}、failures[]、important_changes[]（审计权重高的字段变化）。

## 5. 企业补全组（5 个，两条链路）

**链路 A：Excel 信息池批量补空（只补库内企业，不新建不覆盖）**

```
excel_sheets_to_pool_read   多 Sheet 按归一化企业名聚成"信息池"（自动识别 18 位信用代码）
pool_normalize              正则规范化：枚举/bool 关键词/行业/手机/人数/地址（纯内存）
sql_enterprises_backfill    批量只补空字段（dry_run 默认 true；region 可指定）
```

**链路 B：对话式单企业补字段**

```
enterprise_field_schema_get   可提交字段目录（类型/枚举/描述）——权威白名单
sql_enterprise_fields_commit  强校验 + 只补空；数组字段取并集只增不删；dry_run 默认 true
```

写入侧统一安全约束：dry_run 默认开启、身份键不可改、枚举/类型强校验、补全链路只填空不覆盖、不新建企业。

## 6. 企业名消歧与匹配

查询侧（sql_inspection_records_query）：enterprise_keyword 按 name/code 模糊匹配——

- 0 家 → 返回空结果 + hint"未找到名称含 X 的企业"；
- **多家 → 不静默取第一条**，返回 `ambiguous:true` + 前 5 家候选（id/name/region/industry/address），要求向用户确认全称后重试；
- 恰好 1 家才用其 id 查隐患。

写入侧五级 resolve（import_writer.EnterpriseWriter）：信用代码精确 → 名称精确 → 归一化名精确（全角转半角、去空白）→ 归一化双向包含相似（双方≥6字、长度差≤4 且唯一候选）→ 地址/行业交叉验证；企业名末尾厂房号（`#9#10#`）自动剥离。

## 7. 知识库组（11 个，返回纯文本）

| 工具 | 写? | 说明 |
|---|---|---|
| rag_docs_search | 读 | 单库稠密+稀疏双路 → RRF → 重排，Top10 片段（文件名 + 相关度 + 正文前 800 字） |
| rag_docs_aggregate | 读 | 查询变体 × 多集合扩检，约 30 条，供 Agent 汇总 |
| qdrant_docs_list | 读 | 列集合内文档（文件名/chunk 数，前 30） |
| qdrant_doc_content_get | 读 | 按文件名取切片原文（默认 20 块） |
| file_parse_preview | 读 | 索引前预检：PDF 页数 + 前 3 页采样判断扫描件，不写任何库 |
| qdrant_file_index | **写 Qdrant** | 单文件解析 → 切片 → 双路向量入库；replace 替换 |
| qdrant_directory_index | **写 Qdrant** | 目录递归批量索引；async_mode 返回 task_id |
| qdrant_index_status_get | 读 | 查异步任务进度（状态在进程内存） |
| qdrant_doc_info_get | 读 | 查文档 chunk 数/来源（删除前核实） |
| qdrant_doc_delete | **写 Qdrant** | 删除 dense+sparse 两路向量；两阶段确认（confirm 默认 false）；不删磁盘文件 |
| qdrant_collections_list | 读 | 列全部集合名 |

内部链路（宿主机路径映射、双集合、payload、召回算法）见 [Qdrant 解析与召回](qdrant解析与召回.md)。

## 8. 读写边界与审计现状

- 会写 **PostgreSQL** 的工具仅 4 个：`sql_record_commit`（可新建）、`sql_record_flag`、`sql_enterprises_backfill`、`sql_enterprise_fields_commit`，且后三者默认 dry_run；全部走 svc_Import 的校验/匹配逻辑。
- 会写 **Qdrant** 的 3 个：`qdrant_file_index`、`qdrant_directory_index`、`qdrant_doc_delete`（带 replace/confirm 开关）。
- **MCP 通道的写操作不写 audit_log 表**（不经过 FastAPI 中间件），可追溯性来自：工具返回给 Agent 的批次报告（进程内存，重启清空）+ 数据库最终状态。平台网页侧（rag-api）的写操作才有审计日志。
- rag-mcp 连续 3 次连接失败后 Hermes 会进入 parked 状态，需在 Hermes 侧手动重启网关。

## 9. rag-api 与 rag-mcp 分工

| | rag-api（:8000） | rag-mcp（:8001） |
|---|---|---|
| 调用方 | 前端浏览器（JWT） | Hermes Agent（MCP 协议，无 JWT） |
| 职责 | 登录鉴权、业务 CRUD、复合筛选查询、Excel HTTP 导入、材料登记/索引任务、问答 SSE 代理、证据分片 | 30 个 Agent 工具：台账只读问答、受控写库、知识库检索与索引 |
| 数据库 | 读写（8 张表），写操作审计 | 只读查询 + 4 个受控写入工具（不审计） |
| Qdrant | 材料索引任务、证据/元数据 | 检索、目录索引、删除等全部知识库操作 |
| 典型响应 | 统一 `{success, data}` JSON | SQL 组 JSON；知识库组给人读的纯文本 |
| 模型 | 启动预热 bge-m3/reranker | 首次工具调用懒加载（各持一份） |
