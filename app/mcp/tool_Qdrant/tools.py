"""知识库组 MCP 工具（11 个，纯 Qdrant，不碰 SQL）—— 检索/清单/索引/删除。

首行标签约定：
  【知识库·检索】rag_docs_search / rag_docs_aggregate —— 只读语义检索，返回纯文本片段
  【知识库·清单】qdrant_collections_list / qdrant_docs_list / qdrant_doc_content_get
             / qdrant_doc_info_get —— 只读浏览库内容
  【知识库·索引】file_parse_preview（预检）/ qdrant_file_index / qdrant_directory_index
             / qdrant_index_status_get —— 写库前先预检，写库后用 task_id 查进度
  【知识库·删除】qdrant_doc_delete —— 破坏性写操作，两阶段确认（confirm 默认 false）
注意：本组工具返回值全部是"给人读的纯文本"，不是 JSON。
handler 实现在同包 handlers.py（签名 `(args: dict) -> str`，转发 app.core_Qdrant）。
"""
from __future__ import annotations

from app.mcp.tool_Qdrant.handlers import (
    _tool_search,
    _tool_list_docs,
    _tool_get_content,
    _tool_preview,
    _tool_index_file,
    _tool_index_dir,
    _tool_status,
    _tool_delete,
    _tool_doc_delete,
    _tool_aggregate,
    _tool_list_collections,
)


def register_knowledge_tools(mcp) -> None:
    """把知识库组 11 个工具注册到 FastMCP 实例。"""

    @mcp.tool()
    def rag_docs_search(
        query: str,
        collection: str | None = None,
        filters: dict | None = None,
    ) -> str:
        """【知识库·检索】单库语义检索：Dense+Sparse 双路召回 + RRF 融合 + Cross-Encoder 重排，取前 10 个片段。

        何时用：用户要在文档资料里找答案/找原文，如"知识库里危化品管理怎么规定的"。
            也是企业补全文档链路的第 1 步（检索企业资料原文）。
        何时不要用：
          - 要一次横跨多个知识库/需要更全的多轮检索 → 改用 rag_docs_aggregate
          - 只想知道库里有哪些文件 → 改用 qdrant_docs_list
          - 查 SQL 台账/企业/巡查 → 用【台账】组工具，本工具只搜文档向量库
        输入字段：
            query: 自然语言或关键词，越具体越好。
            collection: Qdrant 集合名，不传用默认 rag_documents。
            filters: 可选 metadata 过滤，如 {"source_format": "pdf"}，支持 * 通配符。
        返回（纯文本，非 JSON）：
            多条结果用空行分隔，每条格式：
            [序号] 文件名 (相关度: 0.xxx)
            正文前 800 字；无结果返回"未找到…"提示。
        边界：只读 Qdrant，不调 LLM、不改索引；片段是原文截取，需要整篇原文用 qdrant_doc_content_get。
        """
        return _tool_search({"query": query, "collection": collection, "filters": filters})


    @mcp.tool()
    def rag_docs_aggregate(
        query: str,
        collection: str | None = None,
        collections: list[str] | None = None,
        filters: dict | None = None,
    ) -> str:
        """【知识库·检索】跨文档/跨集合聚合检索：多轮多变体召回 + 跨文档去重，取回更广的相关片段（约 30 条）。

        何时用：问题需要综合多份文件，或不确定该搜哪个库，如"汇总所有库里关于有限空间作业的规定"。
        与 rag_docs_search 的选择：单库事实查询用 search（快、10 条）；
            跨集合/主题宽泛/要尽量查全用本工具（慢一些、30 条、自动 query 变体扩检）。
        输入字段：
            query: 分析主题或问题。
            collection: 单集合名，不传用默认 rag_documents。
            collections: 多个集合名，跨库聚合时传；传入后优先于 collection。
            filters: 可选 metadata 过滤。
        返回（纯文本，非 JSON）：
            首行"共聚合 N 个知识库，多轮检索去重后取…M 条内容："，
            之后每条格式同 rag_docs_search（[序号] 文件名 (相关度: x.xxx) + 正文前 800 字）。
        边界：只读，不调 LLM、不改索引；结论由调用方 Agent 基于返回原文自行综合。
        """
        return _tool_aggregate({
            "query": query,
            "collection": collection,
            "collections": collections,
            "filters": filters,
        })


    @mcp.tool()
    def qdrant_docs_list(collection: str | None = None) -> str:
        """【知识库·清单】列出某集合里已索引的文档（文件名 + chunk 数 + 来源格式），最多列前 30 个。

        何时用：用户问"知识库里有哪些文件/某个文件在不在"，或检索前先确认库内容。
        何时不要用：要文件正文 → qdrant_doc_content_get；要查文件内容条文 → rag_docs_search；
            要查有哪些集合 → qdrant_collections_list。
        输入字段：
            collection: Qdrant 集合名，不传用默认 rag_documents。
        返回（纯文本，非 JSON）：
            首行"知识库 X 共 N 个文档:"（N 为总数，可能大于实际列出的 30），
            每行 "- 文件名 (M chunks, source: 格式)"；空库返回提示语。
        边界：只读，不返回正文、不返回服务器路径。
        """
        return _tool_list_docs({"collection": collection})


    @mcp.tool()
    def qdrant_doc_content_get(
        filename: str,
        collection: str | None = None,
        max_chunks: int = 20,
    ) -> str:
        """【知识库·清单】按文件名取已索引文档的正文片段（按 chunk 顺序），用于通读整篇原文。

        何时用：rag_docs_search 命中某文件后用户要"看完整内容/读这个文件"，
            或需要引用某文件连续原文时。
        何时不要用：还不知道文件叫什么 → 先 qdrant_docs_list；
            只要和某问题相关的片段 → rag_docs_search（带重排，更省 token）。
        输入字段：
            filename: 文件名精确匹配，如 'report.pdf'。
            collection: Qdrant 集合名，不传用默认 rag_documents。
            max_chunks: 最多返回 chunk 数，默认 20（每 chunk 截取前 800 字）。
        返回（纯文本，非 JSON）：
            首行"文档《文件名》的内容片段："，之后每块"[Chunk i] 正文（≤800 字）"，空行分隔；
            文件名不存在返回"未找到文档: …"。
        边界：只读；截断输出，超长文档不会一次返回全部。
        """
        return _tool_get_content({
            "filename": filename,
            "collection": collection,
            "max_chunks": max_chunks,
        })


    @mcp.tool()
    def file_parse_preview(file_path: str) -> str:
        """【知识库·索引预检】快速查看服务器文件的基本信息与可读性（不解析、不分块、不写任何库）。

        何时用：索引前确认文件能否处理——尤其判断 PDF 是文字版还是扫描件（扫描件需先 OCR）。
        注意：本工具不跑完整解析，不返回 chunk 数；只做秒级基本检查。
        输入字段：
            file_path: 服务器文件绝对路径，如 /app/data/uploads/report.pdf；
                宿主机路径（/mnt/...）会自动映射为容器内路径。
        返回（纯文本，非 JSON）：
            通用：文件名 / 大小 / 格式后缀。
            PDF：总页数 + 前 3 页文本采样（能提取到文字→文字层可提取；
                提取不到→提示可能是扫描件，需 MinerU OCR）。
            TXT/Markdown：前 1000 字采样。其他格式：仅基本信息 + 索引提示。
        边界：只读文件系统，不碰 Qdrant/SQL；确认可读后再调 qdrant_file_index。
        """
        return _tool_preview({"file_path": file_path})


    @mcp.tool()
    def qdrant_file_index(
        file_path: str,
        collection: str | None = None,
        replace: bool = False,
    ) -> str:
        """【知识库·索引】把单个文件写入 Qdrant（解析→分块→BGE-M3 向量化→入库），会真实修改知识库。

        何时用：用户明确要求把某文件加入知识库/重建某文件索引。建议先 file_parse_preview 预检。
        输入字段：
            file_path: 服务器文件绝对路径（/mnt/... 宿主机路径自动映射）。
            collection: 必填的目标集合，不传用默认 rag_documents；写入前务必确认集合名，
                不确定先调 qdrant_collections_list 看现有集合。
            replace: true=先删该文件旧索引再重建；false（默认）时若文件已在库中会直接跳过。
        返回（纯文本）：
            成功"✓ 已索引: 文件名，N 个 chunk…"；已存在→跳过提示并建议 replace=true；
            在排除目录中→跳过；其他失败返回错误原因。
        边界：写 Qdrant；支持 PDF/Word/Excel/PPT/Markdown/TXT；不写 SQL 材料登记表。
        """
        return _tool_index_file({
            "file_path": file_path,
            "collection": collection,
            "replace": replace,
        })


    @mcp.tool()
    def qdrant_directory_index(
        directory: str,
        collection: str | None = None,
        replace: bool = False,
        async_mode: bool = False,
    ) -> str:
        """【知识库·索引】递归批量索引整个目录的所有支持文件到同一 Qdrant 集合，会真实修改知识库。

        何时用：用户要求一次性导入一个目录/迁移知识库。大目录务必 async_mode=true。
        输入字段：
            directory: 服务器目录绝对路径（/mnt/... 宿主机路径自动映射）。
            collection: 必填的目标集合，不传用默认 rag_documents；不确定先调 qdrant_collections_list。
            replace: true=全部强制重建；false（默认）保留已有。
            async_mode: true=后台执行，立即返回 task_id，随后用 qdrant_index_status_get 轮询；
                false（默认）同步跑完才返回，大目录可能超时。
        返回（纯文本）：
            异步："✓ 已启动异步索引…文件数 N / task_id: xxx"；
            同步："✓ 索引完成…文件数/成功/跳过/失败/chunks"汇总。
        边界：写 Qdrant，遵循全局排除目录规则；任务状态只存进程内存，容器重启丢失。
        """
        return _tool_index_dir({
            "directory": directory,
            "collection": collection,
            "replace": replace,
            "async_mode": async_mode,
        })


    @mcp.tool()
    def qdrant_index_status_get(task_id: str) -> str:
        """【知识库·索引】查异步目录索引任务（qdrant_directory_index async_mode=true 返回的）的实时进度。

        何时用：发起异步批量索引后轮询，直到状态 done/failed。
        输入字段：
            task_id: 异步索引启动时返回的 12 位任务号。
        返回（纯文本，非 JSON）：
            多行文本：任务号 / 状态（pending/running/done/failed）/ 进度 /
            当前文件 / 成功/失败/跳过计数；任务号无效返回错误提示。
        边界：只读进程内存中的任务状态；容器重启后任务与状态全部丢失。
        """
        return _tool_status({"task_id": task_id})


    @mcp.tool()
    def qdrant_doc_info_get(filename: str, collection: str | None = None) -> str:
        """【知识库·清单】查某文档是否已索引（chunk 数 + 来源路径）；只读，不执行删除。

        何时用：确认文件在不在库里/索引了多少块；删除前先用本工具核实目标。
        输入字段：
            filename: 文件名精确匹配，如 'report.pdf'。
            collection: Qdrant 集合名，不传用默认 rag_documents。
        返回（纯文本，非 JSON）：
            命中："文件「x」在知识库 Y 中：chunk 数 / 来源路径"，并提示删除走
            qdrant_doc_delete（confirm=false 预演 → confirm=true 执行）；
            未命中返回"知识库中不存在此文件"。
        边界：只读，不删除任何数据。
        """
        return _tool_delete({"filename": filename, "collection": collection})


    @mcp.tool()
    def qdrant_doc_delete(
        filename: str,
        collection: str | None = None,
        confirm: bool = False,
    ) -> str:
        """【知识库·删除】删除某文档的全部索引（dense+sparse 两路向量），破坏性操作，两阶段确认。

        何时用：用户明确要求把某文件从知识库移除（不删磁盘原文件，只解除索引）。
        强制流程（不得跳过）：
          1. 先用 qdrant_doc_info_get 核实文件名、chunk 数、来源路径；
          2. 本工具 confirm=false（默认）只回显"将删除什么"，把信息告诉用户；
          3. 用户明确同意后，才允许带 confirm=true 再次调用，真正执行删除。
        禁止仅凭模糊文件名或未获用户确认就传 confirm=true。
        输入字段：
            filename: 文件名精确匹配，如 'report.pdf'。
            collection: Qdrant 集合名，不传用默认 rag_documents。
            confirm: false（默认）仅预演不执行；true 真正删除。
        返回（纯文本）：
            confirm=false：⚠ 待删除预演（文件/集合/chunk 数/来源路径 + 不可恢复提示）；
            confirm=true："✓ 已删除…"；文件名不存在则直接提示无需删除。
        边界：删除后不可恢复；只删 Qdrant 向量，不删磁盘文件；
            材料登记表（material 表）里的文件应走材料模块自己的删除接口以保持 SQL 一致。
        """
        return _tool_doc_delete({
            "filename": filename,
            "collection": collection,
            "confirm": confirm,
        })


    @mcp.tool()
    def qdrant_collections_list() -> str:
        """【知识库·清单】列出 Qdrant 中全部知识库集合的名字（无参）。

        何时用：不知道有哪些库/确认目标 collection 名（索引和检索前），如用户问"有哪些知识库"。
        输入字段：无。
        返回（纯文本，非 JSON）：
            "Qdrant 知识库集合:" 后每行 "  - 集合名"；无集合或连不上 Qdrant 返回提示/错误。
            注意：只返回名字，不含各集合文档数（文档数请对具体集合调 qdrant_docs_list）。
        边界：只读 Qdrant 元信息。
        """
        return _tool_list_collections({})
