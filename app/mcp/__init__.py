"""MCP 服务包 —— 把知识库检索/台账查询/Excel导入/企业补全能力暴露为标准 MCP 工具（共 30 个）。

入口：python -m app.mcp.server（streamable-http，容器 rag-mcp:8001）

按数据域分子包：
    tool_Qdrant/  知识库组 11 个 —— 纯 Qdrant：检索/清单/索引/删除（tools.py 定义 + handlers.py 实现）
    tool_Sql/     台账组 6 个   —— 纯 SQL 只读：薄封装 app.services（business.py）
    tool_mix/     综合写入组 13 个 —— Excel 导入（excel_import.py 8 个）+ 企业补全（backfill.py 5 个）
    server.py     服务入口：FastMCP 初始化 + 各组 register
"""
