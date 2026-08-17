"""RAG 检索 MCP Server —— 把知识库检索/索引能力暴露为 MCP 工具。

任何支持 MCP 协议的客户端都能连接本服务（Hermes / Claude Desktop / Claude Code …）：
  - stdio:           python -m app.mcp_server            （本地进程，Claude Desktop 用）
  - streamable HTTP: http://rag-api:8000/mcp             （在 main.py 挂载，Hermes / Claude Code 用）

所有工具复用 tools.py 里现成的 handler（签名 `(args: dict) -> str`），
检索/索引逻辑零重复。工具名、description、参数说明与原注册表保持一致。
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.tools import (
    _tool_search,
    _tool_list_docs,
    _tool_get_content,
    _tool_preview,
    _tool_index_file,
    _tool_index_dir,
    _tool_status,
    _tool_delete,
    _tool_aggregate,
    _tool_list_collections,
)

mcp = FastMCP("rag-search")


@mcp.tool()
def search_knowledge_base(
    query: str,
    collection: str | None = None,
    filters: dict | None = None,
) -> str:
    """在指定知识库集合中检索文档。支持 Dense+Sparse 双路召回 + RRF 融合 + Cross-Encoder 重排。适合：文档问答、内容查询、事实检索。

    Args:
        query: 检索查询，用自然语言或关键词。越具体越好。
        collection: 目标知识库集合名称，如 'workfile' 或 '中医'。不传则使用默认集合。
        filters: 可选 metadata 过滤条件，如 {"source_format": "pdf"}。支持 * 通配符。
    """
    return _tool_search({"query": query, "collection": collection, "filters": filters})


@mcp.tool()
def aggregate_documents(
    query: str,
    collection: str | None = None,
    filters: dict | None = None,
) -> str:
    """跨文档全局分析——多轮检索 + 汇总去重。适合：'总结知识库趋势'、'跨文档统计'。

    Args:
        query: 分析主题或问题。
        collection: 目标知识库集合名称，如 'workfile' 或 '中医'。不传则使用默认集合。
        filters: 可选过滤条件。
    """
    return _tool_aggregate({"query": query, "collection": collection, "filters": filters})


@mcp.tool()
def list_documents(collection: str | None = None) -> str:
    """列出指定知识库集合中所有已索引的文档，显示文件名和 chunk 数量。适合：用户问'有哪些文件'、'知识库有什么'时用。

    Args:
        collection: 目标知识库集合名称，如 'workfile' 或 '中医'。不传则使用默认集合。
    """
    return _tool_list_docs({"collection": collection})


@mcp.tool()
def get_document_content(
    filename: str,
    collection: str | None = None,
    max_chunks: int = 20,
) -> str:
    """获取知识库中某个文档的完整文本内容（按 chunk 返回）。适合：用户要求'给我看某文档的完整内容'时用。

    Args:
        filename: 文档文件名（精确匹配），如 'report.pdf'。
        collection: 目标知识库集合名称。不传则使用默认集合。
        max_chunks: 最多返回多少 chunk，默认 20。
    """
    return _tool_get_content({
        "filename": filename,
        "collection": collection,
        "max_chunks": max_chunks,
    })


@mcp.tool()
def preview_document(file_path: str) -> str:
    """预览一个文件的解析结果——不写入知识库。返回文件类型、chunk 数量、采样内容和元数据。适合：导入前检查文件质量。

    Args:
        file_path: 服务器上的文件绝对路径，如 /app/data/uploads/report.pdf。
    """
    return _tool_preview({"file_path": file_path})


@mcp.tool()
def index_file(
    file_path: str,
    collection: str | None = None,
    replace: bool = False,
) -> str:
    """将单个文件导入知识库（解析→分块→向量写入 Qdrant）。务必指定 collection 参数来导入到正确的知识库。支持 PDF/Word/Excel/PPT/Markdown/TXT。

    Args:
        file_path: 文件绝对路径。
        collection: 目标知识库集合，如 '中医' 或 'workfile'。务必填写！
        replace: 是否先删除旧索引再重建。默认 false。
    """
    return _tool_index_file({
        "file_path": file_path,
        "collection": collection,
        "replace": replace,
    })


@mcp.tool()
def index_directory(
    directory: str,
    collection: str | None = None,
    replace: bool = False,
    async_mode: bool = False,
) -> str:
    """批量索引目录中的所有文件。务必指定 collection 参数来导入到正确的知识库。

    Args:
        directory: 目录绝对路径。
        collection: 目标知识库集合，如 '中医' 或 'workfile'。务必填写！
        replace: 是否强制重建。默认 false。
        async_mode: 异步执行返回 task_id。大目录推荐 true。
    """
    return _tool_index_dir({
        "directory": directory,
        "collection": collection,
        "replace": replace,
        "async_mode": async_mode,
    })


@mcp.tool()
def get_index_status(task_id: str) -> str:
    """查询异步索引任务的进度。返回状态（pending/running/done）、进度（15/50）、当前处理文件等。

    Args:
        task_id: 异步索引发起时返回的 task_id。
    """
    return _tool_status({"task_id": task_id})


@mcp.tool()
def delete_document(filename: str, collection: str | None = None) -> str:
    """查看知识库中文档的详细信息并获取手动删除命令。此工具不执行删除，只返回文件信息和 curl 删除命令，让用户自行决定是否在终端执行。

    Args:
        filename: 文档文件名（精确匹配），如 'report.pdf'。
        collection: 目标知识库集合名称。
    """
    return _tool_delete({"filename": filename, "collection": collection})


@mcp.tool()
def list_collections() -> str:
    """列出 Qdrant 向量数据库中的所有知识库集合（collection）。每个 collection 是一个独立的文档库。"""
    return _tool_list_collections({})


if __name__ == "__main__":
    # 独立跑 streamable-http transport（供 Hermes / Claude Code 通过 url 连接）。
    # 不与 main.py 的 FastAPI 挂载（挂载不传播 lifespan，会报 "Task group is not initialized"）。
    import os

    mcp.settings.host = os.getenv("MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("MCP_PORT", "8001"))
    # DNS rebinding 保护默认只允许 localhost，会拒绝容器名和局域网 IP。
    # 这里直接禁用保护，允许任何 host 访问（供 Docker 容器名 + 局域网机器通过宿主机 IP 连接）。
    mcp.settings.transport_security.enable_dns_rebinding_protection = False
    mcp.run(transport="streamable-http")
