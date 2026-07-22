"""RAG Skill —— 请求预处理器。

所有 /v1/chat/completions 请求自动触发，在进入 Hybrid Agent 之前：
  1. 保留原始 system prompt + 追加 RAG 工具描述
  2. 补齐核心 RAG 工具（search/aggregate/list）到 tools 列表

使用方式（一行）:
    from app.skills import rag_skill
    messages, tools = rag_skill.apply(messages, tools)
"""

from app.tools import CORE_TOOLS, HYBRID_RAG_APPENDIX


class RAGSkill:
    """RAG 预处理技能 —— 无状态，纯函数式。"""

    @staticmethod
    def apply(
        messages: list[dict],
        tools: list[dict] | None,
    ) -> tuple[list[dict], list[dict]]:
        """对请求做 RAG 预处理，返回 (messages, tools) 供 Agent 使用。

        不会修改原参数。
        """
        messages = RAGSkill._augment_system_prompt(messages)
        tools = RAGSkill._ensure_rag_tools(tools)
        return messages, tools

    @staticmethod
    def _augment_system_prompt(messages: list[dict]) -> list[dict]:
        """保留原始 system prompt，追加 RAG 工具描述。"""
        original = next(
            (m for m in messages if m.get("role") == "system"), None
        )
        if original and original.get("content"):
            augmented = original["content"] + HYBRID_RAG_APPENDIX
        else:
            augmented = "你是智能助手。" + HYBRID_RAG_APPENDIX.lstrip("\n")

        return [
            {"role": "system", "content": augmented}
        ] + [m for m in messages if m.get("role") != "system"]

    @staticmethod
    def _ensure_rag_tools(tools: list[dict] | None) -> list[dict]:
        """补齐 RAG 核心工具，不重复。"""
        if not tools:
            tools = []
        existing = {t.get("function", {}).get("name", "") for t in tools}
        for rt in CORE_TOOLS:
            if rt["function"]["name"] not in existing:
                tools.append(rt)
        return tools


# 全局单例
rag_skill = RAGSkill()
