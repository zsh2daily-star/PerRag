"""业务只读 MCP 工具 —— 监管台账问答通道（Hermes 专用，全部只读）。

工具分四个对象域，首行描述均以【台账·xx】标注：
  - sql_enterprises_query             【台账·企业】查企业名单/档案
  - sql_inspection_records_query      【台账·巡查】查隐患明细清单
  - sql_inspection_stats_get          【台账·巡查】隐患计数统计（无清单）
  - sql_inspection_overdue_list       【台账·巡查】超期督办清单（最紧急）
  - sql_materials_query               【台账·材料】查已登记材料文件
  - sql_dashboard_summary_get         【台账·总览】一屏取全全局指标

选择口诀：
  问"有哪些/清单/某企业什么情况" → records_query / enterprises_query
  问"多少/比例/分布"（只要数字）→ stats_get
  问"哪些快逾期了/督办"         → overdue_list
  问"整体情况/概况/给个总览"     → dashboard_summary_get（含前两者摘要，一次取全）

约定：
  - 返回 JSON 字符串；业务逻辑零重复，全部薄封装 service；
  - 全部只读，任何创建/修改/状态流转/打标签都不在本文件（走 Excel 导入或企业补全工具组）。
"""
from __future__ import annotations

import json
from typing import Optional


def _dumps(data) -> str:
    """统一 JSON 序列化（中文不转义，日期转字符串）。"""
    return json.dumps(data, ensure_ascii=False, default=str)


def register_business_tools(mcp) -> None:
    """在给定 FastMCP 实例上注册全部业务只读工具。"""

    @mcp.tool()
    async def sql_enterprises_query(
        keyword: Optional[str] = None,
        industry: Optional[str] = None,
        region: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """【台账·企业】按名称/行业/区域查被监管企业名单与基础档案。

        何时用：用户问企业名单、某类企业有哪些、某企业在哪个区域/地址/法人。
        何时不要用：
          - 问企业的隐患情况 → 改用 sql_inspection_records_query
          - 问材料文件 → 改用 sql_materials_query
          - 问全局数字概况 → 改用 sql_dashboard_summary_get
        输入字段：
            keyword: 企业名称或统一社会信用代码关键词（模糊匹配），如 "华昌"。
            industry: 行业类型精确匹配，如 "化工"、"制造"、"食品"。不传不过滤。
            region: 区域精确匹配，如 "新能源园区"。不传不过滤。
            limit: 最多返回条数，默认 20（最多建议 100，多结果应引导用户细化条件）。
        返回字段（JSON，每条 enterprise）：
            - total / enterprises[]: id / name / code / industry / region / address / legal_person
        业务边界：只读；只返回 7 个基础字段，不含联系人/规模/风险标签等扩展信息。
        """
        from app.services.svc_Ledger.enterprise_service import list_enterprises

        items, total = await list_enterprises(
            page=1, page_size=limit,
            keyword=keyword, industry=industry, region=region,
        )
        return _dumps({
            "total": total,
            "enterprises": [
                {
                    "id": e.id,
                    "name": e.name,
                    "code": e.code,
                    "industry": e.industry,
                    "region": e.region,
                    "address": e.address,
                    "legal_person": e.legal_person,
                }
                for e in items
            ],
        })

    @mcp.tool()
    async def sql_inspection_records_query(
        status: Optional[str] = None,
        enterprise_keyword: Optional[str] = None,
        hazard_level: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """【台账·巡查】查巡查隐患明细清单（一企业一隐患一行），可按企业/状态/等级/关键词过滤。

        何时用：用户要看具体隐患条目，如"华昌有哪些未闭环隐患"、"高危隐患清单"、"消防类问题"。
        何时不要用：
          - 只要数量/分布数字（不要清单）→ 改用 sql_inspection_stats_get
          - 只问超期督办条目           → 改用 sql_inspection_overdue_list
          - 问企业基础档案本身         → 改用 sql_enterprises_query
        输入字段：
            status: open=待处理, rectifying=整改中, closed=已闭环。不传查全部。
            enterprise_keyword: 企业名称关键词（模糊）。
                ⚠ 若该关键词匹配到多家企业，本工具不擅自选一家（防数据串），
                  会返回 ambiguous=true 与候选企业名单，此时必须让用户确认全称后重试。
            hazard_level: low=低危 / medium=中危 / high=高危。
            keyword: 隐患标题或描述关键词，如 "消防"、"漏电"（跨企业/隐患/检查人/批次/备注多列搜索）。
            limit: 最多返回条数，默认 20。
        返回字段（JSON）：
            - total / records[]: 批次、企业名、隐患序号、检查日期、地点、等级、类别、
              隐患描述、状态、整改责任人、整改截止/完成时间、备注
            - 或 {ambiguous: true, candidates: [...], hint: ...} 要求企业名消歧
        业务边界：只读；不返回附件等大字段。
        """
        from app.services.svc_Ledger.enterprise_service import list_enterprises
        from app.services.svc_Ledger.inspection_service import list_records

        enterprise_id = None
        if enterprise_keyword:
            # 多取几条做同名消歧：匹配多家时拒绝静默选第一条（防企业数据串）
            ents, ent_total = await list_enterprises(
                page=1, page_size=5, keyword=enterprise_keyword,
            )
            if ent_total == 0:
                return _dumps({"total": 0, "records": [],
                               "hint": f"未找到名称含'{enterprise_keyword}'的企业"})
            if ent_total > 1:
                return _dumps({
                    "ambiguous": True,
                    "hint": f"'{enterprise_keyword}' 匹配到 {ent_total} 家企业，"
                            f"请向用户确认具体企业全称后重试（以下为前 {len(ents)} 家候选）",
                    "candidates": [
                        {"id": e.id, "name": e.name, "region": e.region,
                         "industry": e.industry, "address": e.address}
                        for e in ents
                    ],
                })
            enterprise_id = ents[0].id

        items, total = await list_records(
            page=1, page_size=limit,
            keyword=keyword, status=status, enterprise_id=enterprise_id,
        )
        if hazard_level:
            items = [r for r in items if r.get("hazard_level") == hazard_level]

        return _dumps({
            "total": total if not hazard_level else len(items),
            "records": [
                {
                    "id": r["id"],
                    "inspection_batch": r["inspection_batch"],
                    "enterprise_code": r.get("enterprise_code"),
                    "enterprise_name": r.get("enterprise_name"),
                    "hazard_index": r.get("hazard_index"),
                    "hazard_count_total": r.get("hazard_count_total"),
                    "title": r["title"],
                    "inspection_date": r.get("inspection_date"),
                    "location": r.get("location"),
                    "region": r.get("region"),
                    "industry": r.get("industry"),
                    "inspectors": r.get("inspectors"),
                    "hazard_level": r.get("hazard_level"),
                    "hazard_category": r.get("hazard_category"),
                    "hazard_description": r["hazard_description"],
                    "status": r["status"],
                    "rect_responsible_name": r.get("rect_responsible_name"),
                    "rect_deadline": r.get("rect_deadline"),
                    "rect_completed_at": r.get("rect_completed_at"),
                    "remark": r.get("remark"),
                }
                for r in items
            ],
        })

    @mcp.tool()
    async def sql_inspection_stats_get() -> str:
        """【台账·巡查】隐患计数统计：各状态数量、超期数量、隐患等级分布（只返回数字，无明细）。

        何时用：用户只问数量/比例/分布，如"未闭环隐患有多少"、"高危几条"、"整改中多少单"。
        何时不要用：
          - 用户要看具体隐患条目 → 改用 sql_inspection_records_query
          - 只关心超期条目清单   → 改用 sql_inspection_overdue_list
          - 还要材料数/本月巡查等全局指标 → 改用 sql_dashboard_summary_get
        输入字段：无
        返回字段（JSON）：
            - status_counts: {open, rectifying, closed}
            - overdue_count: 超期未闭环数
            - level_counts: {low, medium, high}
        业务边界：只读聚合。
        """
        from app.services.svc_Ledger.inspection_service import get_stats

        return _dumps(await get_stats())

    @mcp.tool()
    async def sql_inspection_overdue_list(limit: int = 10) -> str:
        """【台账·巡查】超期督办清单：整改截止日已过仍未闭环的隐患，按截止日升序（最紧急在前）。

        何时用：用户要督办/催办，如"现在有哪些超期单"、"哪些整改快逾期了"。
        何时不要用：
          - 全部待处理/整改中隐患（含未到期）→ 改用 sql_inspection_records_query(status=...)
          - 只要超期数量 → 改用 sql_inspection_stats_get
        输入字段：
            limit: 最多返回条数，默认 10。
        返回字段（JSON，每条只含督办必需字段）：
            - total / records[]: id / enterprise_name / title / hazard_level / status /
              rect_responsible_name（整改责任人）/ rect_deadline（截止日）
        业务边界：只读；不返回隐患描述/位置/类别，聚焦督办。
        """
        from app.services.svc_Ledger.inspection_service import list_overdue

        items = await list_overdue(limit=limit)
        return _dumps({
            "total": len(items),
            "records": [
                {
                    "id": r["id"],
                    "enterprise_name": r.get("enterprise_name"),
                    "title": r["title"],
                    "hazard_level": r["hazard_level"],
                    "status": r["status"],
                    "rect_responsible_name": r.get("rect_responsible_name"),
                    "rect_deadline": r.get("rect_deadline"),
                }
                for r in items
            ],
        })

    @mcp.tool()
    async def sql_materials_query(
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """【台账·材料】查已登记的监管材料文件（规程、标准、模板等）的元信息与索引状态。

        何时用：用户问"有没有危化品规程"、"登记了哪些材料"、"哪些文件索引失败了"。
        何时不要用：
          - 要检索材料里的具体内容/条文 → 改用知识库工具 rag_docs_search
          - 要看全局材料总数指标 → 改用 sql_dashboard_summary_get
        输入字段：
            keyword: 文件名或材料 uid 关键词（模糊），如 "化工"、"规程"。
            status: pending=待处理 / indexing=索引中 / indexed=已索引 / failed=失败。
            limit: 最多返回条数，默认 20。
        返回字段（JSON，每条 material）：
            - total / materials[]: uid / filename / source_format / status /
              scope_industry / scope_region / enterprise_codes / uploaded_by_name / created_at
        业务边界：只读元数据，不返回文件二进制与服务器路径。
        """
        from app.services.svc_Ledger.material_service import list_materials

        items, total = await list_materials(
            page=1, page_size=limit, keyword=keyword, status=status,
        )
        return _dumps({
            "total": total,
            "materials": [
                {
                    "uid": m["uid"],
                    "filename": m["filename"],
                    "source_format": m.get("source_format"),
                    "status": m["status"],
                    "scope_industry": m.get("scope_industry"),
                    "scope_region": m.get("scope_region"),
                    "enterprise_codes": m.get("enterprise_codes", []),
                    "uploaded_by_name": m.get("uploaded_by_name"),
                    "created_at": m.get("created_at"),
                }
                for m in items
            ],
        })

    @mcp.tool()
    async def sql_dashboard_summary_get() -> str:
        """【台账·总览】监管工作台一屏总览：全局指标 + 超期 Top5 + 最近材料 Top5。

        何时用：用户问整体情况、开口式问题，如"本月整体情况"、"给个监管概况"、"现在待办多吗"。
            一次调用即可拿到全貌，不要连续分别调 stats/overdue/materials。
        何时不要用：
          - 用户明确只要某一类明细清单 → 用对应的 records/overdue 工具（明细更长）
          - 用户明确只要某一个计数     → 用 sql_inspection_stats_get（更省 token）
        输入字段：无
        返回字段（JSON）：
            - overdue_count / unclosed_count / monthly_new_inspections / material_total
            - level_counts: {low, medium, high}
            - overdue_list: 前 5 条 [{enterprise_name, title, hazard_level, rect_deadline}]
            - recent_materials: 前 5 条 [{filename, status, created_at}]
        业务边界：只读多源聚合；Top5 截断以控制 token，需要更长清单请调专门工具。
        """
        from app.services.svc_Ledger.dashboard_service import get_dashboard_stats

        stats = await get_dashboard_stats()
        # 控制给 LLM 的体积：超期列表只留 5 条核心字段
        stats["overdue_list"] = [
            {
                "enterprise_name": x.get("enterprise_name"),
                "title": x["title"],
                "hazard_level": x["hazard_level"],
                "rect_deadline": x.get("rect_deadline"),
            }
            for x in stats.get("overdue_list", [])[:5]
        ]
        stats["recent_materials"] = [
            {
                "filename": x["filename"],
                "status": x["status"],
                "created_at": x.get("created_at"),
            }
            for x in stats.get("recent_materials", [])[:5]
        ]
        return _dumps(stats)
