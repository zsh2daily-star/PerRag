"""共享模型加载模块 —— 全局单例的嵌入/重排/稀疏编码器。

indexer.py 和 retriever.py 各自需要加载 BGE-M3 和 Reranker 模型，
统一收敛到此模块，避免重复代码。

所有模型函数都是线程安全的（double-checked locking），
确保多线程环境下只加载一次。
"""

import logging
import os
import threading
from typing import Any

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
_offloaded: set[str] = set()  # 已卸载到 CPU 的模型名

# ── 设备检测 ──────────────────────────────────────────────

# EMBED_DEVICE 环境变量可强制嵌入/重排模型跑 CPU（如 "cpu"），
# 用于释放 GPU 显存给本地 LLM。默认自动检测（有 GPU 用 cuda，否则 cpu）。
DEVICE = os.getenv("EMBED_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")

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
    global _sparse_model, _offloaded
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
    if "sparse" in _offloaded:
        _move_model_to_device(_sparse_model, DEVICE)
        _offloaded.discard("sparse")
        logger.info("稀疏编码器已恢复到 %s", DEVICE)
    return _sparse_model


def get_reranker() -> "FlagReranker":
    """加载 BGE-Reranker-v2-M3 Cross-Encoder 重排模型。

    与 BGE-M3 不同，这是专门做"相关性打分"的模型：
    输入 (query, doc) 对，输出 0~1 的相关性分数。
    """
    global _reranker, _offloaded
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
    if "reranker" in _offloaded:
        _move_model_to_device(_reranker, DEVICE)
        _offloaded.discard("reranker")
        logger.info("重排模型已恢复到 %s", DEVICE)
    return _reranker


def get_embed_model() -> HuggingFaceEmbedding:
    """加载 BGE-M3 稠密向量编码器（用于 LlamaIndex dense 检索）。

    首次加载需要从 HuggingFace 下载模型文件（约 2GB），
    之后缓存到 HF_HOME 目录，重启不必重新下载。

    如果之前被 offload_models_to_cpu() 卸载，自动恢复到 GPU。
    """
    global _embed_model, _offloaded
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
    elif "embed" in _offloaded:
        _move_model_to_device(_embed_model, DEVICE)
        _offloaded.discard("embed")
        logger.info("嵌入模型已恢复到 %s", DEVICE)
    return _embed_model


def _move_model_to_device(outer: Any, device: str) -> bool:
    """尝试将模型对象移动到指定设备（通用探测）。"""
    # 直接支持 .to() 的对象
    if hasattr(outer, "to") and callable(outer.to):
        try:
            outer.to(device)
            return True
        except Exception:
            pass

    # 探测常见属性：_model（llama_index）、model（FlagEmbedding）
    for attr in ("_model", "model"):
        inner = getattr(outer, attr, None)
        if inner is not None and hasattr(inner, "to") and callable(inner.to):
            try:
                inner.to(device)
                return True
            except Exception:
                pass

    return False


def offload_models_to_cpu() -> None:
    """将嵌入/稀疏/重排模型从 GPU 卸载到 CPU，释放显存给 MinerU。

    幂等：已在 CPU 的模型跳过。每个模型独立追踪，
    get_embed_model() 等被调用时自动恢复到 GPU。
    """
    global _offloaded

    freed = False
    for name, ref in [("embed", _embed_model), ("sparse", _sparse_model), ("reranker", _reranker)]:
        if name in _offloaded:
            continue
        if ref is not None and _move_model_to_device(ref, "cpu"):
            _offloaded.add(name)
            logger.debug("  %s → CPU", name)
            freed = True

    if freed:
        import gc
        gc.collect()
        torch.cuda.empty_cache()
        logger.info("模型已卸载到 CPU，GPU 显存已释放")


def reload_models_to_gpu() -> None:
    """将所有已卸载的模型从 CPU 移回 GPU。"""
    global _offloaded
    if not _offloaded:
        return

    for name in list(_offloaded):
        ref = {"embed": _embed_model, "sparse": _sparse_model, "reranker": _reranker}[name]
        if ref is not None and _move_model_to_device(ref, DEVICE):
            _offloaded.discard(name)
            logger.debug("  %s → %s", name, DEVICE)

    torch.cuda.empty_cache()
    logger.info("模型已恢复到 %s", DEVICE.upper())


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