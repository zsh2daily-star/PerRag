"""共享模型加载模块 —— 全局单例的嵌入/重排/稀疏编码器。

indexer.py 和 retriever.py 各自需要加载 BGE-M3 和 Reranker 模型，
统一收敛到此模块，避免重复代码。

所有模型函数都是线程安全的（double-checked locking），
确保多线程环境下只加载一次。
"""

import logging
import threading
from pathlib import Path

import torch
from llama_index.core import Settings
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from app.config import settings

logger = logging.getLogger(__name__)

# ── 全局模型实例 ──────────────────────────────────────────

_sparse_model = None
_reranker = None
_embed_model: HuggingFaceEmbedding | None = None
_lock = threading.Lock()

# ── 设备检测 ──────────────────────────────────────────────

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

logger.info("模型运行设备: %s", DEVICE.upper())
if DEVICE == "cuda":
    gpu_name = torch.cuda.get_device_name(0)
    gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1024**3
    logger.info("GPU: %s (%.1f GB)", gpu_name, gpu_mem)


def get_sparse_model() -> "BGEM3FlagModel":
    """加载 BGE-M3 稀疏向量编码器（词权重，用于关键词匹配检索）。

    与 HuggingFaceEmbedding（稠密向量）对应，这里取 BGE-M3 的另一路输出：
    lexical_weights（词权重），用于关键词级别的精确匹配。

    共享同一个模型权重文件，indexer.py 和 retriever.py 都会用到。
    """
    global _sparse_model
    if _sparse_model is None:
        with _lock:
            if _sparse_model is None:
                from FlagEmbedding import BGEM3FlagModel

                cache_dir = str(settings.models_cache_dir)
                settings.models_cache_dir.mkdir(parents=True, exist_ok=True)
                logger.info("加载 BGE-M3 稀疏编码器: %s (device=%s)",
                            settings.embedding_model, DEVICE)
                _sparse_model = BGEM3FlagModel(
                    settings.embedding_model,
                    cache_dir=cache_dir,
                    devices=[DEVICE],
                )
    return _sparse_model


def get_reranker() -> "FlagReranker":
    """加载 BGE-Reranker-v2-M3 Cross-Encoder 重排模型。

    与 BGE-M3 不同，这是专门做"相关性打分"的模型：
    输入 (query, doc) 对，输出 0~1 的相关性分数。
    """
    global _reranker
    if _reranker is None:
        with _lock:
            if _reranker is None:
                from FlagEmbedding import FlagReranker

                cache_dir = str(settings.models_cache_dir)
                settings.models_cache_dir.mkdir(parents=True, exist_ok=True)
                logger.info("加载重排模型: %s (device=%s)",
                            settings.reranker_model, DEVICE)
                _reranker = FlagReranker(
                    settings.reranker_model,
                    cache_dir=cache_dir,
                    use_fp16=DEVICE == "cuda",
                )
    return _reranker


def get_embed_model() -> HuggingFaceEmbedding:
    """加载 BGE-M3 稠密向量编码器（用于 LlamaIndex dense 检索）。

    首次加载需要从 HuggingFace 下载模型文件（约 2GB），
    之后缓存到 HF_HOME 目录，重启不必重新下载。
    """
    global _embed_model
    if _embed_model is None:
        with _lock:
            if _embed_model is None:
                cache_dir = str(settings.models_cache_dir)
                settings.models_cache_dir.mkdir(parents=True, exist_ok=True)
                logger.info("加载嵌入模型: %s (device=%s)",
                            settings.embedding_model, DEVICE)
                _embed_model = HuggingFaceEmbedding(
                    model_name=settings.embedding_model,
                    cache_folder=cache_dir,
                    device=DEVICE,
                )
                Settings.embed_model = _embed_model
    return _embed_model


def warmup_models(blocking: bool = False) -> None:
    """预热所有模型 —— 在应用启动时调用，加速首次查询。

    首次查询前如果不预热，用户第一个请求会触发模型加载（30-60 秒），
    体验很差。预热将加载时间前置到启动阶段。

    每个模型加载失败不会阻塞其他模型或导致应用崩溃 ——
    加载失败只记录 warning 日志，对应的检索功能在首次使用时
    会再次尝试加载。

    参数:
        blocking: True  = 同步等待所有模型加载完成再返回
                  False = 在后台线程中加载，立即返回（默认，适合 FastAPI startup 事件）
    """
    def _load_all() -> None:
        """后台加载所有模型，逐个加载，失败不中断。"""
        logger.info("模型预热开始...")
        try:
            get_embed_model()
            logger.info("  ✓ 嵌入模型已就绪")
        except Exception as e:
            logger.warning("  ✗ 嵌入模型加载失败: %s", e)

        try:
            get_sparse_model()
            logger.info("  ✓ 稀疏编码器已就绪")
        except Exception as e:
            logger.warning("  ✗ 稀疏编码器加载失败: %s", e)

        try:
            get_reranker()
            logger.info("  ✓ 重排模型已就绪")
        except Exception as e:
            logger.warning("  ✗ 重排模型加载失败: %s", e)

        logger.info("模型预热完成")

    if blocking:
        _load_all()
    else:
        # 后台线程加载，不阻塞 FastAPI 的 uvicorn 启动
        t = threading.Thread(target=_load_all, daemon=True)
        t.start()