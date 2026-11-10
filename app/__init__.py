"""RAG 文档问答系统 —— 基于检索增强生成的智能文档问答后端。

本包提供以下核心能力：

RAG Core 能力层 (app.core_Qdrant) —— 有清晰边界、可替换、可独立演进：
    检索链路:  用户提问 → Dense + Sparse 双路召回 → RRF 融合 → Cross-Encoder 重排
    索引链路:  多格式文档解析 → 文本分块 → Dense + Sparse 双向量编码 → 写入 Qdrant
    对外契约:  retrieve() / index_file() / index_directory() /
              docs_meta() / delete_document() / get_index_status()
    可替换点:  RAG_CORE_LOAD_CONFIG（分块参数、模型名、召回/重排数量、RRF 权重）
    支持 PDF（MinerU OCR / pypdf）、Word、Excel、PPT、Markdown、TXT

MCP 检索服务 (app.mcp 包):
    30 个检索/索引/台账/导入/补全工具暴露为标准 MCP（streamable-http），
    供 Hermes 直连 LLM 时统一编排调用。
    server.py 是入口；tool_Qdrant/ 是知识库组，只转发到 app.core_Qdrant。

API 服务 (app.main + app.config):
    FastAPI REST API（业务台账端点 + 健康检查/证据溯源），
    编排层只面向 core_Qdrant 公开契约，不直接触碰 Qdrant / 模型单例。

模块速览:
    main.py          FastAPI 入口（业务 REST 端点），只做编排
    config.py        全局配置（环境变量 → Settings 不可变数据类）
    core_Qdrant/     Qdrant 基础能力层（解析→分块→向量化→检索，纯技术零业务）
      __init__.py      公开契约（retrieve/index_*/docs_meta 等）
      config.py        RAG_CORE_LOAD_CONFIG：分块/模型/RRF 等可替换点
      parser.py        多格式文档解析（PDF/Word/Excel/PPT/Markdown/TXT）
      mineru_client.py MinerU PDF 解析 HTTP 客户端（GPU OCR）
      indexer.py       文档索引器（解析→分块→双向量写入 Qdrant）
      retriever.py     混合检索器（双路召回→RRF→重排）
      models.py        BGE-M3 Embedding/Sparse + BGE-Reranker 模型单例
      docs_meta.py     文档/集合元数据查询与缓存（docs_meta 契约组）
    mcp/             MCP 服务包（server 入口 + 4 组工具，streamable-http 供 Hermes 调用）
    import_docs.py   CLI 批量导入工具（python -m app.import_docs）
"""
