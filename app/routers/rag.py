"""健康检查与证据溯源端点。

说明：问答 / OpenAI 兼容 / 文档列表 / 聚合分析等旧端点已于 2026-10-01 下线——
问答由 Hermes 经 rag-mcp 工具链承担，文档检索/索引用 MCP 知识库组工具。
本路由只保留平台基础端点：/、/health、/evidence/{material_id}。
"""

from fastapi import APIRouter

router = APIRouter(tags=["RAG"])


@router.get("/")
def root():
    """API 基本信息。"""
    return {"message": "RAG API is running"}


@router.get("/health")
def health():
    """健康检查，返回 {"status": "ok"}。"""
    return {"status": "ok"}


@router.get("/evidence/{material_id}")
async def get_evidence_endpoint(
    material_id: str,
    page: int | None = None,
    collection: str | None = None,
):
    """按 material uid + page 获取原文片段（可追溯引用）。

    路径参数 material_id 实际为材料 uid（V2 稳定身份）。
    """
    from app.core_Qdrant import get_evidence

    chunks = get_evidence(material_id, page=page, collection=collection)
    return {
        "material_uid": material_id,
        "page": page,
        "total": len(chunks),
        "chunks": chunks,
    }
