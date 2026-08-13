"""混合检索与问答模块 —— 稠密+稀疏双路召回 → RRF 融合 → Cross-Encoder 重排 → Ollama 生成。

完整 RAG 检索链路:
  用户提问
    ├─ 1. Dense 检索:  BGE-M3 稠密向量 → Qdrant dense 集合 → Top N_dense（语义匹配）
    ├─ 2. Sparse 检索: BGE-M3 稀疏词权重 → Qdrant sparse 集合 → Top N_sparse（关键词匹配）
    ├─ 3. RRF 融合:   双路结果按倒排秩融合去重 → Top M
    ├─ 4. Re-ranker:  BGE-Reranker-v2-M3 Cross-Encoder 精排 → Top K
    └─ 5. 生成:      拼接上下文 + 问题 → Ollama 大模型生成回答

为什么需要双路检索？
- Dense（稠密向量）：语义相似度搜索，能理解"人工智能"和"机器学习"是相关概念
- Sparse（稀疏词权重）：精确的关键词匹配，能区分"苹果公司"和"水果苹果"
- 两者互补：Dense 召回率高但精确率低，Sparse 精确率高但召回面窄，融合后综合最优

为什么需要 Cross-Encoder 重排？
- Bi-Encoder（BGE-M3 编码阶段）：分离编码 query 和 doc，速度快但交互不深
- Cross-Encoder（Reranker 重排阶段）：把 query+doc 拼在一起编码，精细对比但慢
- 策略：Bi-Encoder 粗筛 30 条，Cross-Encoder 细排 Top 10 → 5，兼顾速度与质量
"""

import json
import logging
import threading
from typing import Any

import httpx
from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.config import resolve_api_key, settings
from app.models import get_embed_model, get_reranker, get_sparse_model

logger = logging.getLogger(__name__)


def _extract_text_from_payload(payload: dict | None) -> str:
    """从 Qdrant point payload 中提取文本内容。

    LlamaIndex QdrantVectorStore 把文本存在 _node_content JSON 的 text 字段中，
    但部分旧数据或直接写入的 point 可能有扁平的 text 字段。两者都尝试。
    """
    if not payload:
        return ""
    # 优先扁平的 text 字段（兼容直接写入的 point）
    if payload.get("text"):
        return payload["text"]
    # LlamaIndex 存在 _node_content JSON 里
    nc_raw = payload.get("_node_content")
    if nc_raw:
        try:
            nc = json.loads(nc_raw) if isinstance(nc_raw, str) else nc_raw
            return nc.get("text", "") or ""
        except (json.JSONDecodeError, TypeError):
            return ""
    return ""


# 发给大模型的提示词模板
# {context} 中的每段文档以 [N] 编号，LLM 回答时用 [N] 标注引用来源
RAG_PROMPT = """\
基于以下参考文档回答问题。如果无法从文档中找到答案，请说明你不知道。

重要：回答中引用文档内容时，请在句末标注来源编号，如 [1]、[2][3]。
这样读者能知道每句话出自哪个文档。

参考文档:
{context}

问题: {question}
回答:"""


# ── 检索器 ────────────────────────────────────────────────


class Retriever:
    """混合检索器：稠密 + 稀疏双路召回 + RRF 融合 + Cross-Encoder 重排。"""

    def __init__(self):
        self._client: QdrantClient | None = None
        self._indices: dict[str, VectorStoreIndex] = {}

    def _get_client(self) -> QdrantClient:
        if self._client is None:
            self._client = QdrantClient(
                host=settings.qdrant_host,
                port=settings.qdrant_port,
            )
        return self._client

    def _get_dense_index(self, collection: str) -> VectorStoreIndex:
        """获取稠密检索索引（按 collection 缓存）。"""
        if collection not in self._indices:
            embed_model = get_embed_model()
            vector_store = QdrantVectorStore(
                client=self._get_client(),
                collection_name=collection,
            )
            self._indices[collection] = VectorStoreIndex.from_vector_store(
                vector_store, embed_model=embed_model,
            )
        return self._indices[collection]

    def _sparse_collection(self, collection: str) -> str:
        return f"{collection}_sparse"

    # ── 过滤器构建 ──────────────────────────────────────

    @staticmethod
    def _build_filters(filters: dict[str, str] | None) -> qmodels.Filter | None:
        """将用户友好的过滤条件转为 Qdrant Filter。

        输入格式:
            {"filename": "*.pdf", "source_format": "pdf", "source": "*招商*"}

        支持通配符（*）—— 转为 Qdrant 的 wildcard match。
        无通配符的值做精确匹配。
        多个条件用 AND（must）组合。

        返回 None 表示无过滤条件。
        """
        if not filters:
            return None

        conditions = []
        for key, value in filters.items():
            if "*" in value:
                # 通配符匹配：将 * 替换为 Qdrant 的任意字符模式
                pattern = value.replace("*", "")
                conditions.append(
                    qmodels.FieldCondition(
                        key=key,
                        match=qmodels.MatchText(text=pattern),
                    )
                )
            else:
                conditions.append(
                    qmodels.FieldCondition(
                        key=key,
                        match=qmodels.MatchValue(value=value),
                    )
                )

        return qmodels.Filter(must=conditions) if conditions else None

    # ── 稠密检索（Dense）─────────────────────────────────

    def _dense_search(
        self, query: str, top_k: int, collection: str,
        qdrant_filter: qmodels.Filter | None = None,
    ) -> list[dict]:
        """用 BGE-M3 稠密向量在 Qdrant 做语义相似度搜索。

        当有过滤条件时，直接用 Qdrant client 查询（支持 payload 过滤）。
        无过滤条件时走 LlamaIndex retriever（更快，有缓存）。

        返回每个结果的 text、source、filename、score。
        score 是余弦相似度，范围 0~1，越高越相似。
        """
        # 无过滤 → 用 LlamaIndex retriever（快路径）
        if qdrant_filter is None:
            index = self._get_dense_index(collection)
            retriever = index.as_retriever(similarity_top_k=top_k)
            nodes = retriever.retrieve(query)
            return [
                {
                    "text": node.node.text,
                    "source": node.node.metadata.get("source", ""),
                    "filename": node.node.metadata.get("filename", ""),
                    "score": round(node.score or 0, 4),
                }
                for node in nodes
            ]

        # 有过滤 → 直接用 Qdrant client 查询（支持 payload filter）
        # 必须指定 using="text-dense"（LlamaIndex QdrantVectorStore 的默认向量名），
        # 否则 qdrant-client 1.18+ 的 query_points 会报 "Not existing vector name error"
        embed_model = get_embed_model()
        query_vector = embed_model.get_query_embedding(query)

        client = self._get_client()
        results = client.query_points(
            collection_name=collection,
            query=query_vector,
            using="text-dense",
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )

        return [
            {
                "text": _extract_text_from_payload(p.payload),
                "source": p.payload.get("source", "") if p.payload else "",
                "filename": p.payload.get("filename", "") if p.payload else "",
                "score": round(p.score, 4),
            }
            for p in results.points
        ]


    def _sparse_search(
        self, query: str, top_k: int, collection: str,
        qdrant_filter: qmodels.Filter | None = None,
    ) -> list[dict]:
        """用 BGE-M3 稀疏词权重在 Qdrant 做关键词匹配搜索。

        原理：
        1. BGE-M3 将 query 转为词权重字典 {词ID: 权重, ...}
        2. Qdrant 用稀疏向量索引做高效匹配
        3. 权重高的词在文档中出现越多，得分越高

        返回格式与 _dense_search 一致。
        """
        # 检查稀疏集合是否存在
        sparse_collection = self._sparse_collection(collection)
        client = self._get_client()
        if not client.collection_exists(sparse_collection):
            logger.warning("稀疏集合 %s 不存在，跳过稀疏检索", sparse_collection)
            return []

        # 编码 query 的稀疏向量
        model = get_sparse_model()
        outputs = model.encode([query], return_sparse=True)
        query_sparse = outputs["lexical_weights"][0]

        if not query_sparse:
            return []

        # Qdrant 稀疏向量搜索（支持 payload 过滤）
        try:
            results = client.query_points(
                collection_name=sparse_collection,
                query=qmodels.SparseVector(
                    indices=list(query_sparse.keys()),
                    values=list(query_sparse.values()),
                ),
                using="text",
                limit=top_k,
                query_filter=qdrant_filter,
                with_payload=True,
            )
        except Exception as e:
            logger.warning("稀疏搜索失败（降级为仅 Dense 检索）: %s", e)
            return []

        return [
            {
                "text": _extract_text_from_payload(point.payload),
                "source": point.payload.get("source", "") if point.payload else "",
                "filename": point.payload.get("filename", "") if point.payload else "",
                "score": round(point.score, 4),
            }
            for point in results.points
        ]

    # ── RRF 融合（Reciprocal Rank Fusion）─────────────────

    @staticmethod
    def _rrf_fusion(
        dense_results: list[dict],
        sparse_results: list[dict],
        k: int = 60,
    ) -> list[dict]:
        """RRF（倒数秩融合）—— 将稠密和稀疏两路结果合并去重，不依赖原始分数。

        RRF 公式: score(doc) = Σ_{ranklist} 1/(k + rank_i)
        其中 rank_i 是文档在第 i 个排序列表中的位置（从 0 开始）。

        为什么用 RRF 而不是加权求和？
        - 稠密分数的范围是 0~1（余弦相似度），稀疏分数没有固定范围
        - 直接加权需要归一化，而且不同查询的分数分布差异很大
        - RRF 只看排名不看分数大小，更稳定、更公平

        去重策略：同一个文件来源 + 文本前 200 字符相同视为同一文档片段，
        保留两路中排名更高的那个。
        """
        rrf_scores: dict[str, float] = {}
        doc_map: dict[str, dict] = {}

        def _dedup_key(doc: dict[str, Any]) -> str:
            """生成去重 key：按文本的前 200 字符去重（兼顾效率和准确性）。"""
            return doc["text"][:200]

        # 稠密路：分数 = dense_weight / (k + rank)
        dw = settings.rrf_dense_weight
        for rank, doc in enumerate(dense_results):
            key = _dedup_key(doc)
            score = dw / (k + rank + 1)
            if key not in rrf_scores:
                rrf_scores[key] = score
                doc_map[key] = {**doc, "dense_rank": rank + 1, "sparse_rank": None}
            else:
                # 已存在同内容文档，取高分
                if score > rrf_scores[key]:
                    rrf_scores[key] = score
                    doc_map[key] = {**doc, "dense_rank": rank + 1, "sparse_rank": None}

        # 稀疏路：分数 = sparse_weight / (k + rank)，如需累加权重可调 .env
        sw = settings.rrf_sparse_weight
        for rank, doc in enumerate(sparse_results):
            key = _dedup_key(doc)
            score = sw / (k + rank + 1)
            if key not in rrf_scores:
                rrf_scores[key] = score
                doc_map[key] = {**doc, "dense_rank": None, "sparse_rank": rank + 1}
            else:
                # RRF 默认累加两路分数（更好的融合），已存在的更新 rank 标记
                rrf_scores[key] += score
                doc_map[key]["sparse_rank"] = rank + 1

        # 按 RRF 分数降序排列
        merged = sorted(
            [{"key": k, "rrf_score": round(s, 4), **doc_map[k]} for k, s in rrf_scores.items()],
            key=lambda x: x["rrf_score"],
            reverse=True,
        )
        return merged

    # ── Cross-Encoder 重排 ─────────────────────────────────

    def _rerank(self, query: str, documents: list[dict]) -> list[dict]:
        """用 BGE-Reranker-v2-M3 对候选文档做精排。

        Bi-Encoder vs Cross-Encoder 的区别：
        - Bi-Encoder（BGE-M3）：query 和 doc 各自独立编码为向量 → 快速，但交互不深
        - Cross-Encoder（Reranker）：query 和 doc 拼在一起送入模型 → 慢，但能捕捉细节关联

        策略：Bi-Encoder 粗筛几十条 → Cross-Encoder 细排 Top K → 大幅提升精确率。
        """
        if not documents:
            return documents

        # 构建 (query, doc) 对
        pairs = [[query, doc["text"]] for doc in documents]

        # 分批计算分数（避免 OOM）
        reranker = get_reranker()
        batch_size = settings.rerank_batch_size
        all_scores: list[float] = []
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i : i + batch_size]
            batch_scores = reranker.compute_score(batch)
            if isinstance(batch_scores, float):
                all_scores.append(batch_scores)
            else:
                all_scores.extend(batch_scores)

        # 写入分数并按 Cross-Encoder 分数降序排列
        for doc, score in zip(documents, all_scores):
            doc["rerank_score"] = round(float(score), 4)

        documents.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        return documents

    # ── HyDE：假设文档嵌入 ──────────────────────────────────

    def _hyde_expand(self, query: str) -> str:
        """HyDE（Hypothetical Document Embedding）—— 检索前让 LLM 生成"假设答案"。

        用于抽象/模糊问题的检索优化。LLM 生成的假设答案包含具体术语和细节，
        向量检索时能匹配到更相关的文档片段。

        例如: "什么样的项目算好项目？"
               → LLM 生成 "具备市场前景、技术可行、团队经验丰富的项目..."
               → 用这个答案做向量检索（而非原始问题）

        仅在 settings.hyde_enabled=True 时生效。会增加一次额外的 LLM 调用（约 1-3 秒）。
        """
        prompt = f"""请用一段话简要回答以下问题（作为检索优化的中间步骤，不是最终回答）。

问题: {query}

请生成一段包含具体关键词和概念的描述性文本:"""

        try:
            answer = self._call_llm(prompt)
            return answer.strip() or query
        except Exception:
            return query  # HyDE 失败则降级为原始 query

    # ── 纯检索（不含重排和生成）────────────────────────────

    def retrieve(
        self, query: str, collection: str, top_k: int | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[dict]:
        """执行完整的混合检索链路：Dense + Sparse → RRF → Rerank。

        返回 Top K 条经过重排的文档片段，供外部直接使用或继续调 ask()。

        参数:
            top_k: 返回数量，None 时使用 settings.rerank_top_k（默认 5）。
                   aggregate 场景可传更大值（如 30）以扩大召回范围。
            filters: 可选的 payload 过滤条件，如 {"source_format": "pdf"}。
                     多个条件用 AND 组合，支持 * 通配符。
        """
        qdrant_filter = self._build_filters(filters)

        # HyDE 检索优化：用假设答案代替原始问题做向量搜索
        if settings.hyde_enabled:
            expanded = self._hyde_expand(query)
            if expanded != query:
                logger.info("HyDE 检索: 原始=%s → 扩展=%s",
                            query[:80], expanded[:80])
                query = expanded

        # 两路并行召回（传入过滤条件）
        dense_results = self._dense_search(
            query, settings.retrieval_top_k, collection,
            qdrant_filter=qdrant_filter,
        )
        sparse_results = self._sparse_search(
            query, settings.retrieval_top_k, collection,
            qdrant_filter=qdrant_filter,
        )

        logger.info(
            "双路召回: dense=%d, sparse=%d", len(dense_results), len(sparse_results)
        )

        # RRF 融合去重
        merged = self._rrf_fusion(dense_results, sparse_results)

        # BGE-Reranker 精排
        reranked = self._rerank(query, merged)

        # 返回指定数量的 Top K
        return reranked[: (top_k or settings.rerank_top_k)]

    # ── 完整问答 ──────────────────────────────────────────

    def ask(
        self,
        query: str,
        collection: str | None = None,
        filters: dict[str, str] | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_api_base: str | None = None,
        llm_api_key: str | None = None,
    ) -> dict:
        """RAG 问答：混合检索 → 拼接上下文 → 大模型生成回答。

        参数:
            query: 用户问题
            collection: 目标 Qdrant collection 名称，None=使用默认值
            filters:   可选 payload 过滤条件，如 {"source_format": "pdf"}
            llm_provider: 覆盖默认 LLM 提供方（ollama/api），None=不覆盖
            llm_model:    覆盖默认模型名
            llm_api_base: 覆盖默认 API 地址
            llm_api_key:  覆盖默认 API 密钥

        返回:
            dict: {"query": ..., "answer": ..., "sources": [...]}
        """
        col = collection or settings.qdrant_collection
        # 第一步：混合检索获取相关文档（支持 metadata 过滤）
        documents = self.retrieve(query, col, filters=filters)

        if not documents:
            return {"query": query, "answer": "未找到相关文档。", "sources": []}

        # 第二步：拼接上下文提示词
        context_parts = []
        for i, doc in enumerate(documents, 1):
            source_info = doc["filename"]
            if doc.get("rerank_score"):
                source_info += f" (relevance: {doc['rerank_score']})"
            context_parts.append(f"[{i}] 来源: {source_info}\n{doc['text']}")

        context = "\n\n".join(context_parts)
        prompt = RAG_PROMPT.format(context=context, question=query)

        # 第三步：调用大模型生成回答
        try:
            answer = self._call_llm(prompt, llm_provider, llm_model, llm_api_base, llm_api_key)
        except RuntimeError:
            answer = "大模型服务调用失败，请检查服务状态。"

        return {
            "query": query,
            "answer": answer.strip() if answer else "",
            "sources": [
                {
                    "text": doc["text"],
                    "source": doc["source"],
                    "filename": doc["filename"],
                    "score": doc.get("rerank_score") or doc["score"],
                    "dense_rank": doc.get("dense_rank"),
                    "sparse_rank": doc.get("sparse_rank"),
                }
                for doc in documents
            ],
        }

    def _call_llm(
        self,
        prompt: str,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_api_base: str | None = None,
        llm_api_key: str | None = None,
    ) -> str:
        """调用大模型生成回答。

        根据 provider 自动选用对应默认值：
        - ollama: model 默认取 OLLAMA_DEFAULT_MODEL
        - api:    model/base/key 默认取 API_DEFAULT_* 配置
        """
        provider = llm_provider or settings.llm_provider

        if provider == "api":
            base = llm_api_base or settings.api_default_base
            model = llm_model or settings.api_default_model
            key = llm_api_key or resolve_api_key(model)
            return self._call_api(prompt, base, key, model)

        # ollama（默认）
        model = llm_model or settings.ollama_default_model
        return self._call_ollama_api(prompt, model)

    def _call_ollama_api(self, prompt: str, model: str) -> str:
        """Ollama 本地接口。"""
        url = f"{settings.ollama_base_url}/api/generate"
        payload = {"model": model, "prompt": prompt, "stream": False}
        response = httpx.post(url, json=payload, timeout=120)
        response.raise_for_status()
        return response.json().get("response", "")

    def _call_api(self, prompt: str, base: str, key: str, model: str) -> str:
        """OpenAI 兼容接口（DeepSeek、Groq、vLLM 等）。"""
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        response = httpx.post(url, json=payload, headers=headers, timeout=120)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    # ── 流式 LLM 调用 ──────────────────────────────────────

    def _stream_ollama(self, prompt: str, model: str):
        """从 Ollama /api/generate 流式逐 token 获取。"""
        url = f"{settings.ollama_base_url}/api/generate"
        payload = {"model": model, "prompt": prompt, "stream": True}
        with httpx.stream("POST", url, json=payload, timeout=120) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line:
                    try:
                        token = json.loads(line).get("response", "")
                        if token:
                            yield token
                    except json.JSONDecodeError:
                        continue

    def _stream_api(self, prompt: str, base: str, key: str, model: str):
        """从 OpenAI 兼容 API 流式逐 token 获取。"""
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "stream": True,
        }
        with httpx.stream("POST", url, json=payload, headers=headers, timeout=120) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

    def stream_llm(
        self,
        prompt: str,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_api_base: str | None = None,
        llm_api_key: str | None = None,
    ):
        """流式调用 LLM，逐 token yield。

        根据 provider 自动选用 Ollama 或 OpenAI 兼容流式后端。
        """
        provider = llm_provider or settings.llm_provider
        if provider == "api":
            base = llm_api_base or settings.api_default_base
            model = llm_model or settings.api_default_model
            key = llm_api_key or resolve_api_key(model)
            for token in self._stream_api(prompt, base, key, model):
                yield token
        else:
            model = llm_model or settings.ollama_default_model
            for token in self._stream_ollama(prompt, model):
                yield token

    # ── Chat 风格 LLM 调用（带 messages + tools 透传）────────

    def chat_llm(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_api_base: str | None = None,
        llm_api_key: str | None = None,
    ) -> dict:
        """用完整的 messages 数组调用 LLM，支持 tool calling 透传。

        与 _call_llm（单条 prompt）的区别：
        - 接受完整的 messages 列表（含 system/user/assistant/tool 多轮）
        - 支持 tools 参数透传给 LLM
        - 返回 dict 包含 content 和 tool_calls，而非裸字符串

        返回格式:
            {"content": str|None, "tool_calls": list|None, "finish_reason": str}
        """
        provider = llm_provider or settings.llm_provider

        if provider == "api":
            base = llm_api_base or settings.api_default_base
            model = llm_model or settings.api_default_model
            key = llm_api_key or resolve_api_key(model)
            return self._call_api_chat(messages, tools, base, key, model)

        # ollama（默认）
        model = llm_model or settings.ollama_default_model
        return self._call_ollama_chat(messages, tools, model)

    def stream_chat_llm(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_api_base: str | None = None,
        llm_api_key: str | None = None,
    ):
        """流式 chat LLM —— 产出 OpenAI 兼容的 SSE delta dict。

        每个 yield 返回一个 dict，可直接序列化为 SSE data 行中的 delta:
            {"content": "你好"}  或  {"tool_calls": [...]}

        与 stream_llm（yield 裸字符串 token）的区别：
        - 接受完整的 messages 数组
        - 支持 tools 透传
        - Ollama /api/chat 流式格式自动转为 OpenAI delta 格式
        - yield 的是 delta dict 而非裸 token 字符串
        """
        provider = llm_provider or settings.llm_provider
        if provider == "api":
            base = llm_api_base or settings.api_default_base
            model = llm_model or settings.api_default_model
            key = llm_api_key or resolve_api_key(model)
            yield from self._stream_api_chat(messages, tools, base, key, model)
        else:
            model = llm_model or settings.ollama_default_model
            yield from self._stream_ollama_chat(messages, tools, model)

    # ── Ollama /api/chat 后端 ──────────────────────────────

    def _call_ollama_chat(
        self, messages: list[dict], tools: list[dict] | None, model: str,
    ) -> dict:
        """Ollama /api/chat —— 对话风格 LLM 调用，支持 messages + tools。"""
        url = f"{settings.ollama_base_url}/api/chat"
        payload: dict = {
            "model": model, "messages": messages, "stream": False
        }
        if tools:
            payload["tools"] = tools

        response = httpx.post(url, json=payload, timeout=120)
        response.raise_for_status()
        data = response.json()
        msg = data.get("message", {})
        return {
            "content": msg.get("content"),
            "tool_calls": msg.get("tool_calls"),
            "finish_reason": "stop" if data.get("done", True) else "error",
        }

    def _stream_ollama_chat(
        self, messages: list[dict], tools: list[dict] | None, model: str,
    ):
        """Ollama /api/chat 流式 → OpenAI SSE delta 格式。

        Ollama chat 流式的原始格式：每行是一个完整的 message 快照
            {"message": {"role": "assistant", "content": "你好"}, "done": false}
            {"message": {"role": "assistant", "content": "你好！"}, "done": false}
            {"message": {"role": "assistant", "content": "你好！"}, "done": true}

        我们计算相邻快照的 diff，转为 OpenAI 的增量 delta 格式：
            {"content": "你好"}  →  {"content": "！"}  →  {} (finish)

        tool_calls 同理：当 message 中出现 tool_calls 时，
        计算 tool_calls 的 diff 并以增量方式 yield。
        """
        url = f"{settings.ollama_base_url}/api/chat"
        payload: dict = {"model": model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools

        prev_content = ""
        prev_tool_calls: list[dict] = []

        with httpx.stream("POST", url, json=payload, timeout=120) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = chunk.get("message", {})
                content = msg.get("content") or ""
                tool_calls = msg.get("tool_calls")
                done = chunk.get("done", False)

                # 计算 content 的增量
                if content and content != prev_content:
                    delta = content[len(prev_content):]
                    prev_content = content
                    if delta:
                        yield {"content": delta}

                # 计算 tool_calls 的增量（首次出现时整组发出）
                if tool_calls and tool_calls != prev_tool_calls:
                    prev_tool_calls = tool_calls
                    yield {"tool_calls": tool_calls}

                if done:
                    break

    # ── OpenAI 兼容 API Chat 后端 ──────────────────────────

    def _call_api_chat(
        self, messages: list[dict], tools: list[dict] | None,
        base: str, key: str, model: str,
    ) -> dict:
        """OpenAI 兼容 /chat/completions —— 对话风格调用。

        与 _call_api（单条 user prompt）的区别：
        - 传递完整的 messages 数组（含多轮历史）
        - 支持 tools / tool_choice 透传
        - 返回包含 content 和 tool_calls 的完整响应
        """
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        response = httpx.post(url, json=payload, headers=headers, timeout=120)
        response.raise_for_status()
        data = response.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")
        finish_reason = choice.get("finish_reason", "stop")

        # 诊断日志：确认 LLM 是返回原生 tool_calls 还是把工具调用写进 content（XML）
        logger = logging.getLogger(__name__)
        logger.info(
            "API chat 响应: finish=%s, tool_calls=%s, content 前 200 字=%.200r",
            finish_reason,
            tool_calls,
            content or "",
        )

        return {
            "content": content,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
        }

    def _stream_api_chat(
        self, messages: list[dict], tools: list[dict] | None,
        base: str, key: str, model: str,
    ):
        """OpenAI 兼容 /chat/completions 流式，直通 delta。

        与 _stream_api 的区别：
        - 传递完整 messages 数组 + tools
        - 不再只提取 delta.content，而是完整透传 delta 对象
          （可同时包含 content 和 tool_calls 片段）
        """
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        with httpx.stream("POST", url, json=payload, headers=headers, timeout=120) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if delta:
                        # 透传 delta 中的所有字段（content、tool_calls 等）
                        yield delta
                except json.JSONDecodeError:
                    continue

    # ── 多轮对话 RAG（带历史消息）───────────────────────────

    @staticmethod
    def _build_conversation_query(messages: list[dict], max_messages: int = 6) -> str:
        """从多轮对话历史中构建检索查询字符串。

        为什么需要这个？单用最后一条 user 消息可能丢失上下文——
        "详细的数字呢？" 不知道指什么。拼接前几轮对话后，
        检索器拿到的是 "A报告讲什么 财务数据 详细的数字"，
        召回质量显著提升。

        策略：
        1. 取最近 N 条消息（默认 6 条 = 3 轮对话）
        2. 提取所有 user 消息 + 最后一条 assistant 消息（用于指代消解）
        3. 用空格拼接在一起

        参数:
            messages: OpenAI 格式的消息列表 [{"role": "...", "content": "..."}]
            max_messages: 最多取最近多少条消息

        返回:
            str: 拼接后的检索查询字符串
        """
        recent = messages[-max_messages:]
        parts: list[str] = []

        for m in recent:
            role = m.get("role", "")
            content = m.get("content") or ""
            if role == "user" and content:
                parts.append(content)

        # 如果最后一条是 assistant 且包含实质性内容，也加入
        # 帮助检索器理解当前讨论的"主题"
        last_non_user = None
        for m in reversed(recent):
            if m.get("role") == "assistant" and m.get("content"):
                last_non_user = m["content"]
                break
        if last_non_user:
            # 只取前 100 个字符，避免检索 query 过长
            parts.append(last_non_user[:100])

        return " ".join(parts[-4:])  # 最多取最近 4 段

    @staticmethod
    def _truncate_messages(
        messages: list[dict], max_turns: int = 10,
    ) -> list[dict]:
        """截断对话历史，保留最近 N 轮（防止上下文超长）。

        策略：保留 system 消息 + 最近 N 轮（每轮 = user + assistant）
        如果消息中有 tool 角色，也一并保留。
        """
        if len(messages) <= max_turns * 2 + 2:
            return messages

        # 保留 system 消息
        system_msgs = [m for m in messages if m.get("role") == "system"]

        # 取最后 max_turns * 2 条非 system 消息
        non_system = [m for m in messages if m.get("role") != "system"]
        recent = non_system[-(max_turns * 2):]

        return system_msgs + recent

    def ask_with_history(
        self,
        messages: list[dict],
        collection: str | None = None,
        filters: dict[str, str] | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_api_base: str | None = None,
        llm_api_key: str | None = None,
    ) -> dict:
        """多轮对话 RAG 问答 —— 使用完整对话历史做检索和生成。

        与 ask()（单条 query）的区别：
        - 用对话历史构建更丰富的检索 query（不只是最后一条 user 消息）
        - 把 RAG 检索到的文档注入为 system 消息，LLM 能看到完整对话上下文
        - LLM 调用时传递完整的 messages 数组（含历史），而非单一 prompt

        流程:
            1. 从 messages 构建检索查询（_build_conversation_query）
            2. 混合检索获取相关文档
            3. 构建 RAG system 消息（含检索到的文档上下文）
            4. chat_llm 调用：system(RAG) + 截断后的历史 → 生成回答

        返回格式同 ask():
            {"query": str, "answer": str, "sources": list[dict]}
        """
        col = collection or settings.qdrant_collection

        # 1. 从对话历史构建检索查询
        search_query = self._build_conversation_query(messages)
        # 如果对话历史不充分（只有一条消息），直接用原内容
        last_user = None
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        final_query = search_query or last_user or ""

        logger.info("多轮检索 query: %s", final_query[:200])

        # 2. 混合检索（支持 metadata 过滤）
        documents = self.retrieve(final_query, col, filters=filters)

        if not documents:
            # 无检索结果时也保留对话历史，让 LLM 根据上下文回答
            truncated = self._truncate_messages(messages)
            result = self.chat_llm(
                truncated, tools=None,
                llm_provider=llm_provider, llm_model=llm_model,
                llm_api_base=llm_api_base, llm_api_key=llm_api_key,
            )
            return {
                "query": final_query,
                "answer": (result.get("content") or "未找到相关文档。").strip(),
                "sources": [],
            }

        # 3. 构建 RAG 上下文（注入为 system 消息）
        context_parts = []
        for i, doc in enumerate(documents, 1):
            source_info = doc["filename"]
            if doc.get("rerank_score"):
                source_info += f" (relevance: {doc['rerank_score']})"
            context_parts.append(f"[{i}] 来源: {source_info}\n{doc['text']}")
        context = "\n\n".join(context_parts)

        system_prompt = RAG_PROMPT.format(context=context, question="{question}")
        # 用最后一条 user 消息替换占位符
        system_prompt = system_prompt.replace("{question}", last_user or "")

        # 4. 拼装消息列表：system(RAG 上下文) + 截断的历史
        rag_system = {"role": "system", "content": system_prompt}
        truncated = self._truncate_messages(messages)
        # 去掉原有的 system 消息（避免冲突），用 RAG system 替换
        chat_messages = [rag_system] + [m for m in truncated if m.get("role") != "system"]

        # 5. 调用 LLM（使用 chat 风格，传递完整消息数组）
        result = self.chat_llm(
            chat_messages, tools=None,
            llm_provider=llm_provider, llm_model=llm_model,
            llm_api_base=llm_api_base, llm_api_key=llm_api_key,
        )

        return {
            "query": final_query,
            "answer": (result.get("content") or "").strip(),
            "sources": [
                {
                    "text": doc["text"],
                    "source": doc["source"],
                    "filename": doc["filename"],
                    "score": doc.get("rerank_score") or doc["score"],
                    "dense_rank": doc.get("dense_rank"),
                    "sparse_rank": doc.get("sparse_rank"),
                }
                for doc in documents
            ],
        }

    # ── 跨文档聚合 ──────────────────────────────────────────

    def aggregate(
        self,
        query: str,
        collection: str | None = None,
        collections: list[str] | None = None,
        filters: dict[str, str] | None = None,
        top_k_per_collection: int = 15,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_api_base: str | None = None,
        llm_api_key: str | None = None,
    ) -> dict:
        """跨知识库聚合统计：扩大检索 → 汇总去重 → LLM 全局分析。

        与 ask() 的区别：
        - ask() 只检索少量相关片段（默认 5 条）做精确回答
        - aggregate() 扩大检索（默认 30 条）+ 扩展查询，保留重排分数，
          汇总后做全局分析，优先采信相关度高的内容

        参数:
            query: 用户问题（如"汇总知识库中关于XX公司的所有信息"）
            collection: 单个 collection 名称
            collections: 多个 collection 列表
            top_k_per_collection: 每个 collection 每轮召回数（已废弃，保留兼容）
            llm_provider/llm_model/llm_api_base/llm_api_key: LLM 配置

        返回:
            dict: {"query": ..., "answer": ..., "sources": [...]}
        """
        # 确定目标 collection 列表
        if collections:
            targets = collections
        elif collection:
            targets = [collection]
        else:
            targets = [settings.qdrant_collection]

        # 第一轮：对每个 collection 扩大检索
        # 用 aggregate_top_k（默认 30）代替 rerank_top_k（默认 5），
        # 保留 Cross-Encoder 重排分数，让 LLM 能优先采信高分片段。
        all_docs: list[dict] = []
        seen_keys: set[str] = set()

        for col in targets:
            try:
                docs = self.retrieve(query, col, top_k=settings.aggregate_top_k, filters=filters)
            except Exception as e:
                logger.warning("aggregate: 检索 collection=%s 失败: %s", col, e)
                continue

            for doc in docs:
                dedup_key = doc["text"][:200]
                if dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
                    all_docs.append(doc)

        logger.info("aggregate 第一轮检索: %d 条（去重后）", len(all_docs))

        if not all_docs:
            return {"query": query, "answer": "未在知识库中找到相关文档。", "sources": []}

        # 第二轮：多角度检索扩展覆盖范围
        # 用回答中可能涉及的角度做补充检索，同样扩大召回
        expansion_queries = [
            f"总结 {query}",
            f"统计 {query}",
            f"{query} 的发展趋势",
        ]

        for col in targets[:2]:
            for eq in expansion_queries[:2]:
                try:
                    docs = self.retrieve(eq, col, top_k=20, filters=filters)
                except Exception:
                    continue

                for doc in docs:
                    dedup_key = doc["text"][:200]
                    if dedup_key not in seen_keys:
                        seen_keys.add(dedup_key)
                        all_docs.append(doc)

        logger.info("aggregate 扩展检索后: %d 条（去重后）", len(all_docs))

        # 按 rerank_score 降序排列 —— 分数高的优先采信
        all_docs.sort(key=lambda x: x.get("rerank_score", x.get("score", 0)), reverse=True)
        top_docs = all_docs[:30]

        # 拼接汇总提示词，标注相关度分数让 LLM 优先采信高分片段
        context_parts = []
        for i, doc in enumerate(top_docs, 1):
            source_info = doc["filename"]
            score = doc.get("rerank_score") or doc.get("score")
            if score:
                source_info += f" (相关度: {score})"
            context_parts.append(f"[{i}] 来源: {source_info}\n{doc['text']}")

        context = "\n\n".join(context_parts)
        agg_prompt = f"""你是一个知识库分析助手。请基于以下 {len(top_docs)} 段参考文档的内容，
对用户提出的问题做出全局性的、汇总式的分析和回答。

重要提示：
- 每段文档后面标注了"相关度"分数，分数越高说明与问题越相关
- 请优先采信和汇总相关度高的文档内容
- 尽量涵盖多个文档来源的观点，提供全面的信息
- 如有统计数据或趋势信息，请一并汇总

参考文档:
{context}

问题: {query}
汇总分析:"""

        try:
            answer = self._call_llm(agg_prompt, llm_provider, llm_model, llm_api_base, llm_api_key)
        except RuntimeError:
            answer = "大模型服务调用失败，无法完成聚合分析。"

        return {
            "query": query,
            "answer": answer.strip() if answer else "",
            "sources": [
                {
                    "text": doc["text"],
                    "source": doc["source"],
                    "filename": doc["filename"],
                    "score": doc.get("rerank_score") or doc["score"],
                    "dense_rank": doc.get("dense_rank"),
                    "sparse_rank": doc.get("sparse_rank"),
                }
                for doc in top_docs
            ],
        }


# ── 全局单例（线程安全的 double-checked locking）─────────


_retriever: Retriever | None = None
_retriever_lock = threading.Lock()


def get_retriever() -> Retriever:
    """获取全局唯一的 Retriever 实例（线程安全）。"""
    global _retriever
    if _retriever is None:
        with _retriever_lock:
            if _retriever is None:
                _retriever = Retriever()
    return _retriever
