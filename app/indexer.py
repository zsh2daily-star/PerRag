"""文档索引模块 —— 将文档解析、分块、转为稠密+稀疏双向量并存入 Qdrant。

核心流程:
    parse_file(path)                      # parser.py: 解析文档为纯文本
      ↓
    SentenceSplitter(chunk_size, overlap) # 将长文本切成小块（chunk）
      ↓
    HuggingFaceEmbedding(BGE-M3)          # 稠密向量（语义匹配）→ Qdrant dense 集合
      ↓
    BGEM3FlagModel.encode(sparse)         # 稀疏向量（关键词匹配）→ Qdrant sparse 集合

混合检索原理:
- Dense  向量：把文本语义转为高维向量，用余弦相似度找"意思相近"的内容
- Sparse 向量：提取文本中的关键词权重，用词频匹配找"用了相同词"的内容
- 双路召回 vs 单路：互补——语义相似但用词不同（dense 能抓到），用词相同但语义不同（sparse 更能区分）
"""

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.config import Settings as AppSettings, settings
from app.models import get_embed_model, get_sparse_model

logger = logging.getLogger(__name__)


# ── 分块器工厂 ──────────────────────────────────────────────


def _create_splitter(
    chunk_size: int, chunk_overlap: int, method: str = "fixed",
) -> SentenceSplitter:
    """根据配置创建分块器。

    fixed:   固定大小分块——所有文档用相同的 chunk_size 切分。
            快，但可能在句子/段落中间截断。

    semantic:语义分块——以段落（双换行）为边界，尽量保持段落完整。
             段落超过 chunk_size 时降级为句子切分。
             适合正式公文、报告、合同等段落结构清晰的文档。
    """
    if method == "semantic":
        # 语义分块：以段落分隔符为主，句子分隔符为辅
        return SentenceSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            paragraph_separator="\n\n\n",  # 优先按段落边界
            secondary_chunking_regex="[^。！？.!?]+[。！？.!?]?",  # 长段落降级句子切分
        )
    # fixed（默认）
    return SentenceSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )


# ── 多模态图片标记 ──────────────────────────────────────────


def _build_image_chunks(text: str, source: str, filename: str) -> list[dict]:
    """从 MinerU 返回的 Markdown 文本中提取图片标记，生成可索引的描述片段。

    MinerU 多模态模式下，Markdown 中会包含图片引用标记（如 ![](images/...)）。
    这些标记被转为可检索的文本描述："[图片] 文档 xxx 第N页 包含图表/插图"。

    返回:
        [{"text": "图片描述", "metadata": {...}}, ...]
    """
    import re

    chunks: list[dict] = []
    # 匹配 MinerU 图片引用：![](images/xxx.png) 或类似格式
    img_pattern = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
    matches = img_pattern.findall(text)

    for i, (alt_text, img_path) in enumerate(matches, 1):
        description = alt_text.strip() if alt_text.strip() else f"图片 {i}"
        img_chunk_text = (
            f"[图片描述] 文档《{filename}》中包含图片: {description}\n"
            f"图片路径: {img_path}\n"
            f"此图片是文档中提取的第 {i} 张图片，可能包含图表、数据可视化或插图。"
        )
        chunks.append({
            "text": img_chunk_text,
            "metadata": {
                "source": source,
                "filename": filename,
                "source_format": "image",
                "image_path": img_path,
                "image_index": i,
                "image_alt": description,
            },
        })

    return chunks


# ── 异步任务状态追踪 ────────────────────────────────────────

# 模块级字典，跟踪所有异步索引任务的状态
# key: task_id (str), value: {"status": "running"|"done"|"failed", "progress": "15/50", ...}
_task_status: dict[str, dict] = {}


def get_task_status(task_id: str) -> dict | None:
    """查询异步索引任务的状态。"""
    return _task_status.get(task_id)


def _set_task_status(task_id: str, **kwargs) -> None:
    """更新任务状态（线程安全由 GIL 保证，dict 操作是原子的）。"""
    if task_id in _task_status:
        _task_status[task_id].update(kwargs)
    else:
        _task_status[task_id] = kwargs


# ── 结果数据类 ────────────────────────────────────────────


@dataclass
class FileIndexResult:
    """单个文件的索引结果。"""
    path: str                  # 文件完整路径
    chunks: int                # 产生的 chunk 数量
    status: str                # "success" / "skipped" / "failed"
    error: str | None = None   # 失败原因


@dataclass
class DirectoryIndexResult:
    """整个目录批量索引的结果汇总。"""
    directory: str
    total_files: int
    indexed: int
    failed: int
    skipped: int
    total_chunks: int
    files: list[FileIndexResult]

    def to_dict(self) -> dict:
        return {
            **{k: v for k, v in asdict(self).items() if k != "files"},
            "files": [asdict(f) for f in self.files],
        }


# ── 文档索引器 ────────────────────────────────────────────


class DocumentIndexer:
    """文档索引器 —— 一站式完成"解析 → 分块 → 稠密+稀疏向量 → 写入 Qdrant"。

    内部维护两套 Qdrant 集合：
    - {collection_name}        稠密向量（Dense），由 LlamaIndex 管理
    - {collection_name}_sparse 稀疏向量（Sparse），直接通过 Qdrant client 管理
    """

    def __init__(self, app_settings: AppSettings | None = None):
        self.settings = app_settings or settings
        self._client: QdrantClient | None = None
        self._embed_model: HuggingFaceEmbedding | None = None
        self._vector_store: QdrantVectorStore | None = None
        self._index: VectorStoreIndex | None = None
        self._collection_override: str | None = None

    @property
    def _collection(self) -> str:
        return self._collection_override or self.settings.qdrant_collection

    @property
    def _sparse_collection(self) -> str:
        return f"{self._collection}_sparse"

    # ── 连接与模型管理 ────────────────────────────────────

    def _get_client(self) -> QdrantClient:
        """获取或创建 Qdrant 客户端连接。"""
        if self._client is None:
            self._client = QdrantClient(
                host=self.settings.qdrant_host,
                port=self.settings.qdrant_port,
            )
        return self._client

    def _get_embed_model(self) -> HuggingFaceEmbedding:
        """获取稠密向量编码器（委托给 app.models 全局单例）。"""
        if self._embed_model is None:
            self._embed_model = get_embed_model()
        return self._embed_model

    # ── 集合管理 ──────────────────────────────────────────

    def _ensure_collection(self, vector_size: int) -> None:
        """确保 Qdrant 中稠密向量 collection 存在，不存在则创建。"""
        client = self._get_client()
        name = self._collection
        existing = {c.name for c in client.get_collections().collections}
        if name not in existing:
            logger.info("创建 Qdrant dense collection: %s (dim=%d)", name, vector_size)
            client.create_collection(
                collection_name=name,
                vectors_config=qmodels.VectorParams(
                    size=vector_size,
                    distance=qmodels.Distance.COSINE,
                ),
            )

    def _ensure_sparse_collection(self) -> None:
        """确保 Qdrant 中稀疏向量 collection 存在，不存在则创建。

        稀疏向量和稠密向量存在不同的集合中，因为 Qdrant 对两者的存储格式不同：
        - 稠密：固定长度的浮点数组 [0.123, 0.456, ...]
        - 稀疏：{(词ID: 权重), ...} 只存非零的维度
        """
        client = self._get_client()
        name = self._sparse_collection
        existing = {c.name for c in client.get_collections().collections}
        if name not in existing:
            logger.info("创建 Qdrant sparse collection: %s", name)
            client.create_collection(
                collection_name=name,
                sparse_vectors_config={
                    "text": qmodels.SparseVectorParams(),
                },
            )

    # ── 索引对象 ──────────────────────────────────────────

    def _get_index(self) -> VectorStoreIndex:
        """获取 LlamaIndex 索引（稠密向量搜索用）。
        
        collection 变化时自动重建索引对象。
        """
        col = self._collection
        if self._index is not None and getattr(self, "_cached_collection", None) == col:
            return self._index

        embed_model = self._get_embed_model()
        sample_vector = embed_model.get_text_embedding("dimension probe")
        self._ensure_collection(len(sample_vector))

        self._vector_store = QdrantVectorStore(
            client=self._get_client(),
            collection_name=col,
        )

        storage_context = StorageContext.from_defaults(vector_store=self._vector_store)
        self._index = VectorStoreIndex.from_vector_store(
            self._vector_store,
            storage_context=storage_context,
            embed_model=embed_model,
        )
        self._cached_collection = col
        return self._index

    # ── 数据删除 ──────────────────────────────────────────

    def _delete_by_filename(self, filename: str) -> None:
        """按文件名删除指定文件在 Qdrant 中的稠密向量记录。"""
        client = self._get_client()
        name = self._collection
        existing = {c.name for c in client.get_collections().collections}
        if name not in existing:
            return

        client.delete(
            collection_name=name,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="filename",
                            match=qmodels.MatchValue(value=filename),
                        )
                    ]
                )
            ),
        )

    def _delete_sparse_by_filename(self, filename: str) -> None:
        """按文件名删除指定文件在 Qdrant 中的稀疏向量记录。"""
        client = self._get_client()
        name = self._sparse_collection
        existing = {c.name for c in client.get_collections().collections}
        if name not in existing:
            return

        client.delete(
            collection_name=name,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="filename",
                            match=qmodels.MatchValue(value=filename),
                        )
                    ]
                )
            ),
        )

    def _filename_exists(self, filename: str) -> bool:
        """检查指定文件名是否已导入过（按 filename 字段匹配，不关心路径）。"""
        client = self._get_client()
        name = self._collection
        existing = {c.name for c in client.get_collections().collections}
        if name not in existing:
            return False

        result = client.count(
            collection_name=name,
            count_filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="filename",
                        match=qmodels.MatchValue(value=filename),
                    )
                ]
            ),
        )
        return result.count > 0

    # ── 稀疏向量写入 ──────────────────────────────────────

    def _insert_sparse_vectors(self, nodes: list, source: str, filename: str) -> int:
        """为每个 chunk 计算 BGE-M3 稀疏向量并写入 Qdrant sparse 集合。

        BGE-M3 的稀疏输出格式：
            lexical_weights: [{词ID: 权重, ...}, ...]
        每个词ID 对应 BGE-M3 的词表中的 token 编号，
        权重表示该词在文档中的重要程度。

        Qdrant 稀疏向量格式：
            SparseVector(indices=[词ID...], values=[权重...])

        参数:
            nodes   : LlamaIndex 分块后的节点列表
            source  : 源文件路径
            filename: 文件名（仅名字部分，用于展示）

        返回:
            int: 写入的稀疏向量条数（应与 nodes 数量相等）
        """
        self._ensure_sparse_collection()

        model = get_sparse_model()
        client = self._get_client()

        # 收集所有 chunk 的文本
        texts = [node.text for node in nodes]

        # 一次编码所有文本（批处理比逐条快很多）
        outputs = model.encode(texts, return_sparse=True)
        sparse_weights_list = outputs["lexical_weights"]  # list[dict[int, float]]

        # 构建 Qdrant 点
        points = []
        for i, (text, weights) in enumerate(zip(texts, sparse_weights_list)):
            if not weights:
                continue  # 跳过无词权重的 chunk

            point_id = uuid4().hex
            points.append(
                qmodels.PointStruct(
                    id=point_id,
                    vector={
                        "text": qmodels.SparseVector(
                            indices=list(weights.keys()),
                            values=list(weights.values()),
                        ),
                    },
                    payload={
                        "source": source,
                        "filename": filename,
                        "text": text,  # 存原文，供混合检索时空融合和重排时使用
                        "chunk_index": i,
                    },
                )
            )

        if points:
            client.upsert(collection_name=self._sparse_collection, points=points)

        logger.info("稀疏向量已写入 %s: %d 条", filename, len(points))
        return len(points)

    # ── 索引单文件 ────────────────────────────────────────

    def index_file(self, path: Path, replace: bool = False,
                   skip_existing: bool = False) -> int:
        """索引单个文件：解析 → 分块 → 稠密向量 + 稀疏向量 → 写入 Qdrant。

        参数:
            path: 文件的绝对路径
            replace: True=先删除该文件的旧索引再写入（用于强制重建）
            skip_existing: True=已导入则跳过（replace 优先级更高）

        返回:
            int: 该文件产生的 chunk 数量（skip 返回 0）
        """
        from app.parser import parse_file

        path = path.resolve()
        source = str(path)

        # skip 模式：检查是否已导入（按文件名匹配，不关心路径）
        if skip_existing and not replace:
            if self._filename_exists(path.name):
                logger.info("⊘ 已导入，跳过: %s", path.name)
                return 0

        parsed = parse_file(path)

        # replace 模式：先清除旧数据（按文件名匹配，稠密 + 稀疏两路）
        if replace:
            self._get_index()
            self._delete_by_filename(path.name)
            self._delete_sparse_by_filename(path.name)

        # 构建元数据
        doc_metadata = {
            "source": source,
            "filename": path.name,
            "source_format": parsed.source_format,
        }
        if parsed.page_count:
            doc_metadata["page_count"] = parsed.page_count
        doc_metadata.update(parsed.metadata)

        # 分块（稠密和稀疏共用同一个分块结果）
        # chunk_method=fixed: 固定大小 SentenceSplitter
        # chunk_method=semantic: 按段落边界分块（段落过长时再用句子切分）
        document = Document(text=parsed.text, metadata=doc_metadata)
        splitter = _create_splitter(
            chunk_size=self.settings.chunk_size,
            chunk_overlap=self.settings.chunk_overlap,
            method=self.settings.chunk_method,
        )
        nodes = splitter.get_nodes_from_documents([document])
        if not nodes:
            raise ValueError(f"文档分块为空: {path.name}")

        # 1. 稠密向量入库（LlamaIndex 负责嵌入 + 写入）
        index = self._get_index()
        index.insert_nodes(nodes)
        logger.info("稠密向量已索引 %s，共 %d 个 chunk", path.name, len(nodes))

        # 2. 稀疏向量入库（BGEM3FlagModel 词权重 + 直接写 Qdrant）
        self._insert_sparse_vectors(nodes, source, path.name)

        # 3. 多模态：如果启用了图片提取，为图片生成描述并索引
        image_count = 0
        if self.settings.multimodal_enabled and parsed.source_format == "pdf":
            image_chunks = _build_image_chunks(parsed.text, source, path.name)
            if image_chunks:
                image_nodes = [
                    Document(text=chunk["text"], metadata=chunk["metadata"])
                    for chunk in image_chunks
                ]
                index.insert_nodes(image_nodes)
                self._insert_sparse_vectors(image_nodes, source, path.name)
                image_count = len(image_chunks)
                logger.info("多模态图片描述已索引 %s，共 %d 条", path.name, image_count)

        return len(nodes) + image_count

    # ── 批量索引目录 ──────────────────────────────────────

    def index_directory(
        self,
        directory: Path | None = None,
        recursive: bool = True,
        replace: bool = False,
        skip_existing: bool = False,
        collection: str | None = None,
        task_id: str | None = None,
    ) -> DirectoryIndexResult:
        """批量索引目录中的所有支持文件。

        参数:
            directory: 目录路径，不传则使用 import_dir 配置
            recursive: 是否包含子目录
            replace: 是否先删除同源文件再索引（强制重建）
            skip_existing: 是否跳过已导入的文件（避免重复）
            collection: 目标 Qdrant collection，None 则使用默认配置
            task_id: 异步任务 ID，传入则实时更新进度到 _task_status

        返回:
            DirectoryIndexResult 包含汇总统计和每个文件的详情
        """
        self._collection_override = collection or None
        from app.parser import collect_files

        directory = (directory or self.settings.import_dir).resolve()
        files = collect_files(directory, recursive=recursive,
                               exclude_paths=self.settings.exclude_path_list)

        results: list[FileIndexResult] = []
        indexed = failed = skipped = total_chunks = 0

        total = len(files)
        # 初始化任务状态
        if task_id:
            _set_task_status(task_id, status="running", progress="0/" + str(total),
                             current_file="", directory=str(directory),
                             indexed=0, failed=0, skipped=0)

        for idx, file_path in enumerate(files, 1):
            logger.info("[%d/%d] 处理中: %s", idx, total, file_path.name)

            # 更新任务进度
            if task_id:
                _set_task_status(task_id, progress=f"{idx}/{total}",
                                 current_file=file_path.name,
                                 indexed=indexed, failed=failed, skipped=skipped)

            try:
                chunks = self.index_file(file_path, replace=replace,
                                         skip_existing=skip_existing)
                if skip_existing and chunks == 0:
                    results.append(
                        FileIndexResult(path=str(file_path), chunks=0, status="skipped",
                                        error="已导入，跳过")
                    )
                    skipped += 1
                    continue
                results.append(
                    FileIndexResult(path=str(file_path), chunks=chunks, status="success")
                )
                indexed += 1
                total_chunks += chunks
                logger.info("[%d/%d] ✓ 成功: %s (%d chunks)", idx, total, file_path.name, chunks)
            except ValueError as exc:
                logger.warning("[%d/%d] ⊘ 跳过: %s — %s", idx, total, file_path.name, exc)
                results.append(
                    FileIndexResult(path=str(file_path), chunks=0, status="skipped", error=str(exc))
                )
                skipped += 1
            except Exception as exc:
                logger.exception("[%d/%d] ✗ 失败: %s", idx, total, file_path.name)
                results.append(
                    FileIndexResult(path=str(file_path), chunks=0, status="failed", error=str(exc))
                )
                failed += 1

        # 标记任务完成
        if task_id:
            _set_task_status(task_id, status="done", progress=f"{total}/{total}",
                             indexed=indexed, failed=failed, skipped=skipped,
                             total_chunks=total_chunks, current_file="")

        return DirectoryIndexResult(
            directory=str(directory),
            total_files=len(files),
            indexed=indexed,
            failed=failed,
            skipped=skipped,
            total_chunks=total_chunks,
            files=results,
        )
