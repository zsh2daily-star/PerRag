"""全局配置模块 —— 从环境变量读取所有服务参数。

这里定义了整个 RAG 系统的配置项，包括：
- Qdrant 向量数据库地址
- MinerU PDF 解析服务地址
- LLM 大模型配置（本地 Ollama / 远程 API 通用）
- 嵌入模型、重排模型与分块参数
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _parse_llm_keys() -> dict[str, str]:
    """解析自定义 LLM Key 映射（从 .env 的 LLM_KEYS 环境变量）。

    这是一个模块级函数，在 Settings 类实例化之前被调用，
    返回值作为 Settings.custom_llm_keys 的默认值。

    .env 示例:
        LLM_KEYS='{"deepseek":"sk-xxx","openai":"sk-yyy","groq":"gsk-zzz"}'

    容错：
    - 环境变量不存在 → 返回空字典
    - JSON 格式错误 → 返回空字典（不抛异常，保证服务可启动）
    """
    raw = os.getenv("LLM_KEYS", "{}").strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def resolve_api_key(model: str) -> str:
    """根据模型名匹配对应的 API Key（模糊匹配）。

    匹配规则（按优先级）：
    1. 遍历 LLM_KEYS，如果 key 的关键词出现在 model 名中（大小写不敏感），返回对应值
       例如 model="deepseek-chat" 匹配 key="deepseek"
    2. 无匹配降级取 LLM_KEYS 中 "deepseek" 的值（最常用的默认远程模型）
    3. 都没有返回空字符串（API 调用时会跳过 Authorization header）

    为什么用模糊匹配而非精确匹配？
    同一个提供方可能有多个模型变体（如 deepseek-chat, deepseek-coder），
    模糊匹配允许一把 key 覆盖所有变体。
    """
    model_lower = model.lower()
    for keyword, key in settings.custom_llm_keys.items():
        if keyword.lower() in model_lower:
            return key
    return settings.custom_llm_keys.get("deepseek", "")


@dataclass(frozen=True)
class Settings:
    """RAG 系统全局配置（不可变，创建后无法修改）。

    frozen=True 意味着：创建后任何字段都不能被修改，避免运行时意外改动配置。
    所有值通过 Settings.from_env() 从环境变量中读取。
    """

    # ── Qdrant 向量数据库 ─────────────────────────────────
    qdrant_host: str            # Qdrant 服务主机名（Docker 内用 "qdrant"，本机用 "localhost"）
    qdrant_port: int            # Qdrant 服务端口，默认 6333
    qdrant_collection: str      # 存储文档向量的集合名称，相当于数据库的"表"

    # ── 嵌入模型 ─────────────────────────────────────────
    embedding_model: str        # HuggingFace 模型名称或本地路径，例如 "BAAI/bge-m3"
    models_cache_dir: Path      # 模型下载缓存目录（避免每次重启重下模型）

    # ── 文档处理 ─────────────────────────────────────────
    import_dir: Path            # 待导入文档的默认目录
    exclude_paths: str | None  # 跳过索引的目录路径，逗号分隔，如 "不解析文件"（相对于 import_dir）
    chunk_size: int             # 文档分块每块最多多少 token（默认 512）
    chunk_overlap: int          # 相邻两块之间的重叠 token 数（默认 50，避免信息断联）
    chunk_method: str           # 分块策略：fixed（固定大小）/ semantic（按段落语义边界）
    pdf_parser: str             # PDF 解析模式：mineru（GPU OCR）/ pypdf（纯文字）/ auto（先 MinerU 后降级）

    # ── MinerU PDF 解析服务 ──────────────────────────────
    mineru_host: str            # MinerU 容器主机名
    mineru_port: int            # MinerU 服务端口（Docker 内部为 8000）
    mineru_backend: str         # MinerU 解析后端，如 "pipeline"
    mineru_lang: str            # OCR 语言，中文用 "ch"
    mineru_timeout: int         # 单次 PDF 解析超时时间（秒）

    # ── Ollama 大模型（本地）──────────────────────────────
    ollama_host: str            # Ollama 服务主机名
    ollama_port: int            # Ollama 服务端口，默认 11434

    # ── LLM 通用配置（本地 Ollama / 远程 API 二选一）─────
    llm_provider: str           # 大模型提供方：ollama（本地） / api（OpenAI 兼容接口）
    ollama_default_model: str   # Ollama 本地默认模型，如 "qwen3:8b"
    api_default_model: str      # API 远程默认模型，如 "deepseek-chat"
    api_default_base: str       # API 远程默认地址

    # ── 按模型名匹配 API Key ──────────────────────────────
    custom_llm_keys: dict       # 自定义键值对，key=模型名关键词，value=对应的 API key
                                 # 例如 {"deepseek": "sk-xxx", "openai": "sk-yyy"}（留空则每次请求传入）

    # ── 检索参数 ─────────────────────────────────────────
    retrieval_top_k: int        # 双路检索每路召回数量（默认 30，两路共 60 条再融合）

    # ── 混合检索与重排 ───────────────────────────────────
    reranker_model: str         # Cross-Encoder 重排模型，例如 "BAAI/bge-reranker-v2-m3"
    rerank_top_k: int           # 重排后保留数量，默认 5
    rerank_batch_size: int      # 重排时每批处理多少对，默认 16
    aggregate_top_k: int        # aggregate 模式单次检索返回数量，默认 30

    # ── RRF 融合权重 ─────────────────────────────────────
    rrf_dense_weight: float     # Dense 路在 RRF 融合中的权重，默认 1.0
    rrf_sparse_weight: float    # Sparse 路在 RRF 融合中的权重，默认 1.0

    # ── 多模态 ───────────────────────────────────────────
    multimodal_enabled: bool    # 启用 MinerU 图片提取和描述，默认关闭
    multimodal_image_dir: Path  # 提取的图片存储目录

    # ── HyDE（假设文档嵌入）──────────────────────────────
    hyde_enabled: bool          # 检索前用 LLM 生成假设答案提升召回质量

    # ── 安全 ─────────────────────────────────────────────
    rag_api_key: str            # API 鉴权密钥，不设置则不启用鉴权

    # ── 计算属性 ─────────────────────────────────────────
    @property
    def exclude_path_list(self) -> list[str]:
        """排除目录路径列表（从逗号分隔字符串解析）。"""
        if not self.exclude_paths:
            return []
        return [d.strip() for d in self.exclude_paths.split(",") if d.strip()]

    @property
    def mineru_base_url(self) -> str:
        """MinerU 服务的完整 HTTP 地址。"""
        return f"http://{self.mineru_host}:{self.mineru_port}"

    @property
    def ollama_base_url(self) -> str:
        """Ollama 服务的完整 HTTP 地址。"""
        return f"http://{self.ollama_host}:{self.ollama_port}"

    @classmethod
    def from_env(cls) -> "Settings":
        """从操作系统环境变量中读取配置，构造 Settings 实例。

        每个字段都有默认值（os.getenv 的第二个参数），
        所以在不设置任何环境变量的情况下也能正常运行。
        """
        project_root = Path(__file__).resolve().parent.parent
        import_dir = Path(os.getenv("IMPORT_DIR", project_root / "data" / "uploads"))
        models_cache_dir = Path(os.getenv("HF_HOME", project_root / "models"))

        return cls(
            qdrant_host=os.getenv("QDRANT_HOST", "localhost"),
            qdrant_port=int(os.getenv("QDRANT_PORT", "6333")),
            qdrant_collection=os.getenv("QDRANT_COLLECTION", "rag_documents"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
            import_dir=import_dir,
            exclude_paths=os.getenv("EXCLUDE_PATHS") or None,
            chunk_size=int(os.getenv("CHUNK_SIZE", "512")),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "50")),
            chunk_method=os.getenv("CHUNK_METHOD", "fixed"),
            models_cache_dir=models_cache_dir,
            mineru_host=os.getenv("MINERU_HOST", "localhost"),
            mineru_port=int(os.getenv("MINERU_PORT", "30001")),
            mineru_backend=os.getenv("MINERU_BACKEND", "pipeline"),
            mineru_lang=os.getenv("MINERU_LANG", "ch"),
            mineru_timeout=int(os.getenv("MINERU_TIMEOUT", "600")),
            pdf_parser=os.getenv("PDF_PARSER", "mineru"),
            ollama_host=os.getenv("OLLAMA_HOST", "localhost"),
            ollama_port=int(os.getenv("OLLAMA_PORT", "11434")),
            llm_provider=os.getenv("LLM_PROVIDER", "ollama"),
            ollama_default_model=os.getenv("OLLAMA_DEFAULT_MODEL", "qwen3:8b"),
            api_default_model=os.getenv("API_DEFAULT_MODEL", "deepseek-chat"),
            api_default_base=os.getenv("API_DEFAULT_BASE", "https://api.deepseek.com"),
            custom_llm_keys=_parse_llm_keys(),
            retrieval_top_k=int(os.getenv("RETRIEVAL_TOP_K", "30")),
            reranker_model=os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
            rerank_top_k=int(os.getenv("RERANK_TOP_K", "5")),
            rerank_batch_size=int(os.getenv("RERANK_BATCH_SIZE", "16")),
            aggregate_top_k=int(os.getenv("AGGREGATE_TOP_K", "30")),
            rrf_dense_weight=float(os.getenv("RRF_DENSE_WEIGHT", "1.0")),
            rrf_sparse_weight=float(os.getenv("RRF_SPARSE_WEIGHT", "1.0")),
            hyde_enabled=os.getenv("HYDE_ENABLED", "false").lower() in ("1", "true", "yes"),
            multimodal_enabled=os.getenv("MULTIMODAL_ENABLED", "false").lower() in ("1", "true", "yes"),
            multimodal_image_dir=Path(os.getenv("MULTIMODAL_IMAGE_DIR", project_root / "data" / "images")),
            rag_api_key=os.getenv("RAG_API_KEY", ""),
        )


# 全局单例：程序启动时执行一次，之后所有模块 import 它即可
settings = Settings.from_env()
