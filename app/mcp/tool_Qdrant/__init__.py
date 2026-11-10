"""知识库组（纯 Qdrant，11 个工具）：文档语义检索/库清单/索引/两阶段确认删除。

- tools.py     —— @mcp.tool 定义（register_knowledge_tools 注册入口）
- handlers.py  —— _tool_* handler 实现（转发 app.core_Qdrant，返回纯文本）
"""
from app.mcp.tool_Qdrant.tools import register_knowledge_tools

__all__ = ["register_knowledge_tools"]
