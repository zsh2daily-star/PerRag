"""企业信用代码 MCP 工具 —— 多源策略查询并补全信用代码。

三个工具：
  1. web_enterprise_code_lookup    单企业查询（返回多源查询模板，只查不写库）
  2. web_enterprises_code_backfill  批量补全（返回缺 code 企业列表 + 每家多源查询模板）
  3. web_enterprise_code_commit     Agent 提交提取的信用代码写库

多源策略（优先级从高到低）：
  ① WebSearch（主）：搜 "{企业名} 统一社会信用代码"
     → 摘要直接含信用代码，多源交叉验证（天眼查/企查查/启信宝/水滴信用）
  ② WebFetch 天眼查：https://www.tianyancha.com/search?key=企业名
     → WebFetch 渲染 JS 后正则提取
  ③ WebFetch 启信宝：https://www.qixin.com/search?key=企业名（备用）

安全约束：
  - 只补 code 为空的企业，已有 code 绝不覆盖
  - 默认 dry_run=true，只预览不写库
  - 信用代码格式校验（18 位、9 开头）
  - 不新建企业
"""
import json
from typing import Optional

from app.services.svc_Common.credit_code_lookup import (
    extract_credit_code,
    validate_credit_code,
    build_search_query,
    build_tianyancha_url,
    build_qixin_url,
)


def _dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def register_credit_code_tools(mcp) -> None:
    """在 FastMCP 实例上注册信用代码查询/补全三个工具。"""

    # ── 工具 1：单企业查询（返回多源查询模板，只查不写库）────────
    @mcp.tool()
    async def web_enterprise_code_lookup(enterprise_name: str) -> str:
        """【信用代码·网络查询】按企业名称返回多源查询模板（只查不写库）。

        多源策略：
          ① WebSearch（主，推荐）：搜 "{企业名} 统一社会信用代码"
             → 摘要直接含信用代码，多源交叉验证
          ② WebFetch 天眼查搜索页（辅）
          ③ WebFetch 启信宝搜索页（备用）

        输入字段：
            enterprise_name: 企业全称（如"盐城东方建设投资股份有限公司"）
        返回字段（JSON）：
            - name: 查询的企业名
            - websearch_query: WebSearch 查询语句（主策略，推荐先用这个）
            - tianyancha_url: 天眼查搜索 URL（辅策略，供 WebFetch）
            - qixin_url: 启信宝搜索 URL（备用策略，供 WebFetch）
            - hint: 查询步骤提示
        边界：只返回查询模板，不读不写 SQL，不发起网络请求。
        """
        return _dumps({
            "name": enterprise_name,
            "websearch_query": build_search_query(enterprise_name),
            "tianyancha_url": build_tianyancha_url(enterprise_name),
            "qixin_url": build_qixin_url(enterprise_name),
            "hint": (
                "① 用 WebSearch(query=websearch_query) → 从摘要提取 18 位代码；"
                "② 未命中则 WebFetch(tianyancha_url) → 正则提取；"
                "③ 仍未命中则 WebFetch(qixin_url)。"
                "提取到 code 后调 web_enterprise_code_commit 写库。"
            ),
        })

    # ── 工具 2：批量补全库内 code 为空的企业 ────────────────
    @mcp.tool()
    async def web_enterprises_code_backfill(
        region: str = "新能源园区",
        dry_run: bool = True,
        limit: int = 100,
    ) -> str:
        """【信用代码·批量补全】返回库内 code 为空的企业列表 + 每家多源查询模板。

        编排：
          ① 本工具获取缺 code 企业列表 + 每家的 WebSearch 查询语句 / 天眼查 / 启信宝 URL
          ② Agent 逐个调 WebSearch(query=...) 搜索，从摘要提取 18 位信用代码
          ③ 未命中可 WebFetch 天眼查/启信宝搜索页备用
          ④ 确认后用 web_enterprise_code_commit 写库

        输入字段：
            region: 目标区域（"新能源园区"/"韩资园"/"光电园"等）
            dry_run: 保留兼容性（本工具始终只返回列表，不直接写库）
            limit: 最多返回多少家，默认 100
        返回字段（JSON）：
            - region / total
            - enterprises[]: {id, name, websearch_query, tianyancha_url, qixin_url}
        边界：只读 SQL 返回列表 + 查询模板，不写库。
        """
        from app.db.database import get_session_factory
        from app.db.models import Enterprise
        from sqlmodel import select, or_

        sf = get_session_factory()
        enterprises = []

        async with sf() as s:
            subjects = (await s.execute(
                select(Enterprise).where(
                    Enterprise.region == region,
                    or_(Enterprise.code.is_(None), Enterprise.code == ""),
                ).limit(limit)
            )).scalars().all()

            for ent in subjects:
                enterprises.append({
                    "id": ent.id,
                    "name": ent.name,
                    "websearch_query": build_search_query(ent.name),
                    "tianyancha_url": build_tianyancha_url(ent.name),
                    "qixin_url": build_qixin_url(ent.name),
                })

        return _dumps({
            "region": region,
            "total": len(enterprises),
            "dry_run": dry_run,
            "enterprises": enterprises,
            "hint": (
                "逐个调 WebSearch(query=websearch_query)，从摘要提取 18 位代码（9 开头），"
                "未命中可 WebFetch tianyancha_url，然后 web_enterprise_code_commit 写库。"
            ),
        })

    # ── 工具 3：提交提取的信用代码（Agent 用 WebSearch/WebFetch 后调用）────
    @mcp.tool()
    async def web_enterprise_code_commit(
        enterprise_id: int,
        credit_code: str,
        dry_run: bool = True,
    ) -> str:
        """【信用代码·提交写库】Agent 用 WebSearch/WebFetch 提取信用代码后，提交写库。

        标准编排：
          ① web_enterprises_code_backfill 获取缺 code 企业列表 + 查询模板
          ② 对每家企业调 WebSearch(query=websearch_query) 搜索
          ③ 从搜索结果摘要中正则提取 18 位信用代码（9 开头）
          ④ 本工具提交提取的信用代码，dry_run=true 预览，false 写库

        输入字段：
            enterprise_id: 企业 ID
            credit_code: 提取的 18 位统一社会信用代码
            dry_run: true（默认）只校验预览；false 真正写库
        返回字段（JSON）：
            - id / name / code / valid / dry_run / written / skipped / reason
        安全约束：
            - 只补 code 为空的企业，已有 code 绝不覆盖
            - 信用代码格式校验（18 位、9 开头）
        """
        from app.db.database import get_session_factory
        from app.db.models import Enterprise
        from sqlmodel import select
        from app.services.svc_Import.import_writer import enterprise_writer

        # 1. 格式校验
        code = (credit_code or "").strip().upper()
        if not validate_credit_code(code):
            return _dumps({
                "id": enterprise_id, "code": credit_code,
                "valid": False, "dry_run": dry_run,
                "written": False, "skipped": False,
                "reason": "信用代码格式不符（需 18 位字母/数字，9 开头）",
            })

        # 2. 查企业
        sf = get_session_factory()
        async with sf() as s:
            ent = (await s.execute(
                select(Enterprise).where(Enterprise.id == enterprise_id)
            )).scalars().first()

            if ent is None:
                return _dumps({
                    "id": enterprise_id, "code": code,
                    "valid": True, "dry_run": dry_run,
                    "written": False, "skipped": False,
                    "reason": f"企业 id={enterprise_id} 不存在",
                })

            # 3. 只补空字段
            if ent.code:
                return _dumps({
                    "id": enterprise_id, "name": ent.name,
                    "code": code, "valid": True, "dry_run": dry_run,
                    "written": False, "skipped": True,
                    "reason": f"已有 code={ent.code}，不覆盖",
                })

            # 4. 写库
            if not dry_run:
                try:
                    await enterprise_writer.write_fields(ent.id, code=code)
                    return _dumps({
                        "id": enterprise_id, "name": ent.name,
                        "code": code, "valid": True, "dry_run": False,
                        "written": True, "skipped": False,
                    })
                except Exception as e:
                    return _dumps({
                        "id": enterprise_id, "name": ent.name,
                        "code": code, "valid": True, "dry_run": False,
                        "written": False, "skipped": False,
                        "reason": f"写库失败: {e}",
                    })

            return _dumps({
                "id": enterprise_id, "name": ent.name,
                "code": code, "valid": True, "dry_run": True,
                "written": False, "skipped": False,
            })
