"""RAG 文档问答系统 —— 基于检索增强生成的智能文档问答后端。

本包提供以下核心能力：

索引链路 (app.indexer + app.parser + app.mineru_client):
    多格式文档解析 → 文本分块 → Dense + Sparse 双向量编码 → 写入 Qdrant
    支持 PDF（MinerU OCR / pypdf）、Word、Excel、PPT、Markdown、TXT

检索链路 (app.retriever + app.models):
    用户提问 → Dense 语义检索 + Sparse 关键词检索 → RRF 融合 → Cross-Encoder 重排 → LLM 生成

MCP 检索服务 (app.mcp_server + app.tools):
    10 个检索/索引工具暴露为标准 MCP（streamable-http），
    供 Hermes / Open WebUI 直连 deepseek 时统一编排调用

API 服务 (app.main + app.config):
    FastAPI REST API + OpenAI 兼容端点（/v1/models, /v1/chat/completions 简单 RAG）

模块速览:
    main.py          FastAPI 入口，REST + OpenAI 兼容端点（简单 RAG）
    config.py        全局配置（环境变量 → Settings 不可变数据类）
    models.py        共享模型单例（BGE-M3 Embedding/Sparse + BGE-Reranker）
    indexer.py       文档索引器（解析→分块→双向量写入 Qdrant）
    retriever.py     混合检索器（双路召回→RRF→重排→LLM生成）
    parser.py        多格式文档解析（PDF/Word/Excel/PPT/Markdown/TXT）
    mineru_client.py MinerU PDF 解析 HTTP 客户端（GPU OCR）
    tools.py         工具注册表（10 个 handler，供 MCP 复用）
    mcp_server.py    MCP 检索服务（streamable-http，供 Hermes 直连调用）
    import_docs.py   CLI 批量导入工具（python -m app.import_docs）
"""
