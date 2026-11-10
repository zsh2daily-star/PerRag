"""RAG 检索 MCP Server —— 入口：FastMCP 初始化 + 注册全部 30 个工具。

任何支持 MCP 协议的客户端都能连接本服务（Hermes / Claude Desktop / Claude Code …）：
  - stdio:           python -m app.mcp.server          （本地进程，Claude Desktop 用）
  - streamable HTTP: http://rag-mcp:8001/mcp             （独立容器，Hermes / Claude Code 用）

工具按数据域分四个子包（本文件只做注册，不内嵌工具定义）：
  tool_Qdrant/    知识库组 11 个 —— 纯 Qdrant：检索/清单/索引/两阶段确认删除
  tool_Sql/       台账组 6 个     —— 纯 SQL 只读：企业/巡查/材料/工作台总览
  tool_mix/       综合写入组 13 个 —— Excel 导入（8）+ 企业补全（5）：Excel 解析 + SQL 写入
  tool_DocExport/ 文书导出组 4 个   —— 按模板填充生成 .docx（交办单 + 复查意见书，各含检查+导出）
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("rag-search")

# 知识库组（纯 Qdrant）：检索/清单/索引/删除，handler 转发 app.core_Qdrant
from app.mcp.tool_Qdrant import register_knowledge_tools
register_knowledge_tools(mcp)

# 台账组（纯 SQL 只读）：企业/巡查/材料/工作台，薄封装 app.services
from app.mcp.tool_Sql import register_business_tools
register_business_tools(mcp)

# 综合写入组：Excel 导入（探查→解析→预览→提交→报告）+ 企业补全（两条独立链路）+ 信用代码网络查询
from app.mcp.tool_mix import register_import_tools, register_backfill_tools, register_credit_code_tools
register_import_tools(mcp)
register_backfill_tools(mcp)
register_credit_code_tools(mcp)

# 文书导出组：按模板填充生成 .docx（交办单等交付文书）
from app.mcp.tool_DocExport import register_doc_export_tools
register_doc_export_tools(mcp)


if __name__ == "__main__":
    # 独立跑 streamable-http transport（供 Hermes / Claude Code 通过 url 连接）。
    # 不与 main.py 的 FastAPI 挂载（挂载不传播 lifespan，会报 "Task group is not initialized"）。
    import os

    mcp.settings.host = os.getenv("MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("MCP_PORT", "8001"))
    # DNS rebinding 保护默认只允许 localhost，会拒绝容器名和局域网 IP。
    # 这里直接禁用保护，允许任何 host 访问（供 Docker 容器名 + 局域网机器通过宿主机 IP 连接）。
    mcp.settings.transport_security.enable_dns_rebinding_protection = False
    # 无状态模式：服务端不再维护 per-session 状态，客户端不必回传 mcp-session-id。
    # 默认 stateful 模式下会话空闲 30 分钟即被回收（SDK DEFAULT_SESSION_IDLE_TIMEOUT=1800），
    # 且会话只存内存、容器重启即失效；客户端若沿用旧 session id 会持续收到
    # 404 "Session not found"（Trae 直连即报"连接被拒绝"）。30 个工具均为纯请求/响应，
    # 不依赖会话内状态或服务端推送，故可安全无状态化。
    mcp.settings.stateless_http = True
    mcp.run(transport="streamable-http")
