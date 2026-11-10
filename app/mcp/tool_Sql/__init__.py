"""台账组（纯 SQL 只读，6 个工具）：企业/巡查/材料/工作台总览查询。

- business.py —— 全部薄封装 app.services，不暴露任何写操作。
"""
from app.mcp.tool_Sql.business import register_business_tools

__all__ = ["register_business_tools"]
