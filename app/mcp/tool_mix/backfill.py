"""企业补全 MCP 工具（以 SQL 主体企业为准，自由聚合 + 正则规范化）。

工具分两条互相独立的链路，按数据来源选择，不要混用：

【链路一：Excel 台账批量补全】（输入是结构化 Excel，走正则，不调 LLM）
  Tool A  excel_sheets_to_pool_read  Excel → 信息池（原始字符串值列表），纯提取不碰 SQL
  Tool 中 pool_normalize              输入 pool + 枚举值域 → 正则 → {字段: 值}
  Tool B  sql_enterprises_backfill    以 SQL 主体企业为准，批量只补空字段写库

【链路二：知识库文档 → 单企业语义补全】（输入是非结构化文档，由 Agent 阅读理解）
  Step 1  rag_docs_search（在 rag MCP 模块注册）检索企业资料原文
  Step 2  enterprise_field_schema_get   取允许提交的字段目录/枚举/类型（本文件）
  Step 3  由 Agent 自己阅读原文、按 schema 组装 {字段: 值} JSON（无专门工具）
  Step 4  sql_enterprise_fields_commit  强校验 → 只补空字段 → dry_run 预览/写库（本文件）

职责边界：
  - 所有写库工具默认 dry_run=true，只预览不写；false 才真正落库。
  - 服务端永远做 schema 强校验（enterprise_fields.validate_fields），不信任 Agent 提交值。
  - 只补 SQL 已存在企业的空字段，不新建企业、不覆盖已有值。
  - 本文件所有工具均不调用 LLM；语义理解由调用方 Agent 完成。

命名约定：
  - excel_ : 读 Excel
  - pool_  : 信息池内存计算（不碰 Excel/SQL）
  - sql_   : 写 SQL
"""
from __future__ import annotations

import json
from typing import Optional

from app.services.svc_Import import backfill_service as bs
from app.services.svc_Import.import_parser import DEFAULT_XLSX_PATH
from app.services.svc_Import.enterprise_fields import get_field_schema, validate_fields


def _dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def register_backfill_tools(mcp) -> None:
    """在 FastMCP 实例上注册补全四工具。"""

    # ── Tool A：Excel → 信息池（纯提取，不碰 SQL）────────────────
    @mcp.tool()
    async def excel_sheets_to_pool_read(
        sheets: Optional[list[str]] = None,
        file_path: str = DEFAULT_XLSX_PATH,
    ) -> str:
        """【企业补全·Excel链路①】把 Excel 多 Sheet 的原始文本按企业名聚合成"信息池"（不碰 SQL）。

        链路定位：Excel 批量补全三步的第 1 步（本工具 → pool_normalize → sql_enterprises_backfill）。
        与 Excel 导入组的区别：那条链路是逐行落库（可新建记录）；本链路只把散落各 Sheet 的
            字符串值收集成池，供正则规范化后"只补库内企业的空字段"。
        输入字段：
            sheets: 要解析的 Sheet 名列表；不传解析全部。
            file_path: 服务器上 Excel 文件绝对路径。
        返回字段（JSON）：
            - count / pool: {企业名key: {name, code, raw_values[], sources[]}}
              不识别列名：一家企业的所有非空字符串值都收进 raw_values；
              名称近似（归一化/厂房号剥离）的多行合并到同一 key。
        边界：纯读 Excel，不读不写 SQL、不碰 Qdrant。
        """
        try:
            pool = bs.build_pool(file_path, sheets=sheets)
            # 精简输出：raw_values 可能很长，这里返回完整但 agent 可截断
            return _dumps({"pool": pool, "count": len(pool)})
        except Exception as e:
            return _dumps({"error": str(e)})

    # ── Tool 中：信息池 → 正则规范化（不写库）────────────────
    @mcp.tool()
    async def pool_normalize(
        pool: dict,
        enum_values: Optional[dict[str, list[str]]] = None,
    ) -> str:
        """【企业补全·Excel链路②】对信息池跑正则规则，规范化为每家企业的 {字段: 值}（纯内存计算）。

        链路定位：第 2 步（excel_sheets_to_pool_read → 本工具 → sql_enterprises_backfill）。
        输入字段：
            pool: 上一工具产出的信息池 dict。
            enum_values: 可选覆盖枚举值域 {字段: [允许值,...]}；不传用内置规则。
        规则：枚举精确命中、风险 bool 关键词、行业关键词、手机号、纯数字人数、地址关键词；
            正则拿不准的字段留空（不会猜），留给后续人工或文档链路处理。
        返回字段（JSON）：
            - count / normalized: {企业名key: {name, code, fields{}, sources[]}}
        边界：纯内存计算，不碰 Excel/SQL/Qdrant。
        """
        try:
            enum = None
            if enum_values:
                enum = {k: tuple(v) for k, v in enum_values.items()}
            normalized = bs.normalize_pool(pool, enum_values=enum)
            return _dumps({"normalized": normalized, "count": len(normalized)})
        except Exception as e:
            return _dumps({"error": str(e)})

    # ── Tool B：以 SQL 主体为准，写库补空字段 ────────────────
    @mcp.tool()
    async def sql_enterprises_backfill(
        normalized: dict,
        region: str = "新能源园区",
        dry_run: bool = True,
        batch_id: str = "",
    ) -> str:
        """【企业补全·Excel链路③】用规范化结果批量补库内企业的空字段（默认只预演，不新建企业）。

        链路定位：第 3 步（excel_sheets_to_pool_read → pool_normalize → 本工具）。
        与 sql_enterprise_fields_commit 的区别（二选一不要混用）：
          本工具=Excel 批量、入参是 pool_normalize 的产物、按区域处理；
          sql_enterprise_fields_commit=知识库文档、单企业、fields 由 Agent 读原文组装。
        输入字段：
            normalized: pool_normalize 产物 {企业名key: {name, code, fields}}。
            region: 目标区域，默认"新能源园区"（只匹配该区域库内企业）。
            dry_run: true（默认）只预演返回将要补的字段；false 真正写库。
            batch_id: 批次号（空则自动生成）。
        匹配：先 code 精确、再归一化名；匹配不上记 matched=false 并跳过。
        返回字段（JSON）：
            - batch_id / dry_run
            - summary: {total, matched, filled, skipped}
            - results[]: {name, matched, filled[], skipped[]}
        边界：只补库内已有企业的空字段，不新建、不覆盖已有值；拿不准/冲突字段计入 skipped。
        """
        from app.db.database import get_session_factory
        from app.db.models import Enterprise
        from sqlmodel import select
        from app.services.svc_Import.import_parser import normalize_name as _norm

        if not batch_id:
            import uuid
            batch_id = f"backfill-{uuid.uuid4().hex[:8]}"

        # 载入 SQL 主体企业
        sf = get_session_factory()
        results = []
        summary = {"total": 0, "matched": 0, "filled": 0, "skipped": 0}

        async with sf() as s:
            subjects = (await s.execute(
                select(Enterprise).where(Enterprise.region == region)
            )).scalars().all()
            summary["total"] = len(subjects)

            # code / 归一化名 → SQL 企业
            code_idx = {e.code: e for e in subjects if e.code}
            name_idx = {_norm(e.name): e for e in subjects}

            # 可补字段（排除身份键 + 不存在的字段）
            fillable_fields = [
                "industry", "address", "legal_person", "person_in_charge",
                "safety_officer", "enterprise_category", "asset_attribute",
                "enterprise_scale", "employee_count", "annual_output_value",
                "fire_level", "confined_space",
                "has_natural_gas", "is_injection_molding", "has_dormitory",
                "has_hazardous_chemicals", "has_hazardous_waste",
                "involves_dust_explosion", "is_new_energy_welding",
                "has_large_storage_tank",
            ]

            for key, item in normalized.items():
                name = item.get("name", "")
                code = item.get("code", "")
                fields = item.get("fields", {})
                # 匹配 SQL 企业
                ent = code_idx.get(code) if code else None
                if ent is None:
                    ent = name_idx.get(_norm(name))
                if ent is None:
                    results.append({"name": name, "matched": False,
                                    "filled": [], "skipped": []})
                    continue
                summary["matched"] += 1

                filled = []
                skipped = []
                updates = {}
                for f in fillable_fields:
                    if f not in fields:
                        continue
                    incoming = fields[f]
                    if incoming is None or incoming == "":
                        continue
                    current = getattr(ent, f)
                    # 只补空字段（bool 字段当前 False 视为"空"，可补 True）
                    if f.startswith("has_") or f.startswith("is_") or f == "involves_dust_explosion":
                        if current is True or incoming is not True:
                            skipped.append(f)
                            continue
                        updates[f] = True
                        filled.append(f)
                    else:
                        # 数组列（enterprise_category / confined_space）空 list 视为未填
                        if current not in (None, "", []):
                            skipped.append(f)
                            continue
                        updates[f] = incoming
                        filled.append(f)

                summary["filled"] += len(filled)
                summary["skipped"] += len(skipped)
                results.append({
                    "name": name, "matched": True,
                    "filled": filled, "skipped": skipped,
                })

                if updates and not dry_run:
                    from app.services.svc_Import.import_writer import enterprise_writer
                    await enterprise_writer.write_fields(ent.id, **updates)

        return _dumps({
            "batch_id": batch_id,
            "dry_run": dry_run,
            "summary": summary,
            "results": results,
        })

    # ── 链路二 Step 2：字段目录查询（只读 meta，不检索不写库）────────────
    @mcp.tool()
    async def enterprise_field_schema_get() -> str:
        """【企业补全·文档链路②】查"企业字段补全"允许提交的字段目录与取值约束（无参只读权威清单）。

        何时必须调用：准备根据知识库文档为某企业补结构化字段、随后调
            sql_enterprise_fields_commit 写库之前，必须先调本工具。
            禁止凭记忆用字段名/枚举值——后端只认本清单，越界值会被拒。
        编排位置（文档链路）：
          ① rag_docs_search                  检索企业资料原文
          ② enterprise_field_schema_get      ← 本工具
          ③ Agent 读原文，按目录组装 {字段: 值}（无专用工具）
          ④ sql_enterprise_fields_commit     先 dry_run=true，确认后写库
        输入字段：无。
        返回字段（JSON）：
            - fields: {字段名: {type, allowed?, description}}
                type ∈ string / enum（单值）/ string_array（多值数组）/ int / bool；
                allowed 仅枚举类出现，值必须逐字选取。
            - rules: 提交时必须遵守的全局规则
        边界：只读元信息，不检索文档、不读企业数据、不写库。
        """
        return _dumps(get_field_schema())

    # ── 链路二 Step 4：单企业字段提交（强校验 + 只补空字段）──────────────
    @mcp.tool()
    async def sql_enterprise_fields_commit(
        enterprise_name: str,
        fields: dict,
        dry_run: bool = True,
    ) -> str:
        """【企业补全·文档链路④】提交 Agent 读知识库原文后组装的单企业字段，强校验 + 只补空（默认只预演）。

        与 sql_enterprises_backfill 的区别（二选一不要混用）：
          - sql_enterprises_backfill：Excel 批量，输入来自 pool_normalize（Excel 链路③）
          - 本工具：单企业，fields 由你读 rag_docs_search 取回的原文后组装（文档链路④）

        标准编排（必须按序）：
          ① rag_docs_search(企业名) 取回资料原文
          ② enterprise_field_schema_get 取合法字段名/枚举/类型
          ③ 阅读原文，只挑能明确推断的字段组装 fields，如
             {"industry": "化工", "enterprise_category": ["劳密"], "employee_count": 120}
          ④ 先 dry_run=true 调本工具，把 to_fill 预览给用户确认
          ⑤ 用户确认后 dry_run=false 真正写库

        输入字段：
            enterprise_name: 企业全称；先精确名匹配，再归一化名匹配。
            fields: {字段名: 值}，键必须来自字段目录。
            dry_run: true（默认）只校验+预览；false 真正写入。
        返回字段（JSON）：
            - name / matched: 是否匹配到库内企业（false 则不写入，附 hint）
            - submitted: 原始提交 / validated: 强校验后存活 / rejected: 被拒字段及原因
            - to_fill: 再经"只补空"过滤后本次可补 / written: 实际写入 / skipped: 已有值跳过
            - dry_run
        服务端安全保证：
          1. 白名单/枚举/类型三重强校验，越界即拒（rejected 透明返回原因）
          2. 只补空字段，已有值绝不覆盖（bool 已有 true 不补）
          3. string_array（如 enterprise_category）并集追加、去重保序、只增不删
          4. 不新建企业；dry_run=true 绝不碰库
        """
        from app.db.database import get_session_factory
        from app.db.models import Enterprise
        from sqlmodel import select
        from app.services.svc_Import.import_parser import normalize_name
        from app.services.svc_Import.import_writer import enterprise_writer
        from app.services.svc_Import.enterprise_fields import field_type_of

        # 1) 强校验，并记录每字段被拒原因（透明反馈给 Agent，便于其修正后重提）
        validated = validate_fields(fields)
        rejected = {}
        for k, v in fields.items():
            if k in validated and validated[k] is not None:
                continue
            ftype = field_type_of(k)
            rejected[k] = {
                "enum": "bad_enum: 值必须逐字取自 enterprise_field_schema_get 的 allowed 列表",
                "string_array": "bad_enum: 提交非空数组，且每项必须逐字属于 allowed 列表",
                "int": "bad_type: 需要非负整数",
                "bool": "bad_type: 需要 true/false",
                "string": "bad_type: 需要非空字符串",
            }.get(ftype, "unknown_field: 不在 enterprise_field_schema_get 字段目录内")

        if not validated:
            return _dumps({
                "name": enterprise_name, "matched": None,
                "submitted": fields, "validated": {}, "rejected": rejected,
                "to_fill": {}, "written": [], "skipped": [], "dry_run": dry_run,
            })

        # 2) 匹配 SQL 主体企业：精确名称 → 归一化名
        sf = get_session_factory()
        async with sf() as s:
            ent = (await s.execute(
                select(Enterprise).where(Enterprise.name == enterprise_name)
            )).scalars().first()
            if ent is None:
                nn = normalize_name(enterprise_name)
                ent = next(
                    (e for e in (await s.execute(select(Enterprise))).scalars().all()
                     if normalize_name(e.name) == nn),
                    None,
                )
            if ent is None:
                return _dumps({
                    "name": enterprise_name, "matched": False,
                    "submitted": fields, "validated": validated, "rejected": rejected,
                    "hint": "SQL 中无此企业，本工具不新建企业；请先确认企业名称",
                })

            # 3) 只补空字段（数组字段并集追加、去重保序、只增不删）
            to_fill: dict = {}
            skipped: list[str] = []
            for f, v in validated.items():
                current = getattr(ent, f, None)
                ftype = field_type_of(f)
                if ftype == "string_array":
                    current_list = list(current) if current else []
                    merged = list(current_list)
                    for tag in v:
                        if tag not in merged:
                            merged.append(tag)
                    if len(merged) == len(current_list):
                        skipped.append(f)
                    else:
                        to_fill[f] = merged
                elif ftype == "bool":
                    if current is True or v is not True:
                        skipped.append(f)
                    else:
                        to_fill[f] = True
                else:
                    if current not in (None, ""):
                        skipped.append(f)
                    else:
                        to_fill[f] = v

            written: list[str] = []
            if to_fill and not dry_run:
                await enterprise_writer.write_fields(ent.id, **to_fill)
                written = list(to_fill.keys())

            return _dumps({
                "name": enterprise_name, "matched": True,
                "submitted": fields, "validated": validated, "rejected": rejected,
                "to_fill": to_fill, "written": written, "skipped": skipped,
                "dry_run": dry_run,
            })
