"""RAG Core 配置 —— 检索流水线的"可替换点"集中处（阶段 A4）。

RAG_CORE_LOAD_CONFIG 把检索内核的可调参数收敛到一个地方：
- 分块参数（chunk_size / chunk_overlap / chunk_method）
- 模型名（embedding_model / reranker_model / 模型缓存目录）
- 召回与重排数量（retrieval_top_k / rerank_top_k / rerank_batch_size / aggregate_top_k）
- RRF 融合权重（rrf_dense_weight / rrf_sparse_weight）
- Qdrant 连接与默认 collection
- HyDE / 多模态开关

所有值来源于 app.config.settings（环境变量），Core 内部只读这一份配置。
LLM（ollama / api）属于**边界外可插拔能力**，不在本配置内，
仍由 app.config.settings 提供（retriever._call_llm 使用）。
"""

from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, settings


@dataclass(frozen=True)
class RagCoreConfig:
    """RAG Core 运行配置（不可变）。

    由全局 Settings 派生，字段含义与 app.config.Settings 中对应项一致。
    未来替换检索模型 / 调整检索参数，只需改这一处映射或环境变量。
    """

    # ── Qdrant 向量数据库 ─────────────────────────────────
    qdrant_host: str
    qdrant_port: int
    qdrant_collection: str

    # ── 模型 ─────────────────────────────────────────────
    embedding_model: str       # Dense 嵌入 + Sparse 词权重（BGE-M3）
    models_cache_dir: Path     # HuggingFace 模型缓存目录
    reranker_model: str        # Cross-Encoder 重排模型

    # ── 分块参数 ─────────────────────────────────────────
    chunk_size: int
    chunk_overlap: int
    chunk_method: str

    # ── 召回 / 重排数量 ───────────────────────────────────
    retrieval_top_k: int       # 双路检索每路召回数量
    rerank_top_k: int          # 重排后保留数量
    rerank_batch_size: int     # 重排批处理大小
    aggregate_top_k: int       # aggregate 模式单次检索返回数量

    # ── RRF 融合权重 ─────────────────────────────────────
    rrf_dense_weight: float
    rrf_sparse_weight: float

    # ── 检索开关 ─────────────────────────────────────────
    hyde_enabled: bool         # HyDE 假设文档扩展
    multimodal_enabled: bool   # MinerU 图片提取与描述
    multimodal_image_dir: Path

    @classmethod
    def from_settings(cls, s: Settings) -> "RagCoreConfig":
        """从全局 Settings 派生 Core 配置。"""
        return cls(
            qdrant_host=s.qdrant_host,
            qdrant_port=s.qdrant_port,
            qdrant_collection=s.qdrant_collection,
            embedding_model=s.embedding_model,
            models_cache_dir=s.models_cache_dir,
            reranker_model=s.reranker_model,
            chunk_size=s.chunk_size,
            chunk_overlap=s.chunk_overlap,
            chunk_method=s.chunk_method,
            retrieval_top_k=s.retrieval_top_k,
            rerank_top_k=s.rerank_top_k,
            rerank_batch_size=s.rerank_batch_size,
            aggregate_top_k=s.aggregate_top_k,
            rrf_dense_weight=s.rrf_dense_weight,
            rrf_sparse_weight=s.rrf_sparse_weight,
            hyde_enabled=s.hyde_enabled,
            multimodal_enabled=s.multimodal_enabled,
            multimodal_image_dir=s.multimodal_image_dir,
        )


# 全局单例：程序启动时从环境变量派生一次，Core 内部统一引用
RAG_CORE_LOAD_CONFIG = RagCoreConfig.from_settings(settings)
