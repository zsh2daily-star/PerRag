"""Excel 台账导入 MCP 工具（Hermes 专用）—— 把服务端 Excel 文件按行解析后写入业务表。

【适用场景】用户要求把服务器上的 Excel 台账（企业汇总表/巡查表/材料表）导入数据库。
【不适用】从知识库文档补单个企业字段 → 改用"企业补全"工具组
          （enterprise_field_schema_get + sql_enterprise_fields_commit）。

标准编排（按序，每一步都有对应工具，首行均以【Excel导入·x】标注）：
  ① excel_sheets_list        看有哪些 Sheet
  ② excel_workbook_plan      多 Sheet 工作簿：一次出全簿导入计划（推荐）
     或 excel_sheet_inspect   只导单个 Sheet：探测结构 + 建议列映射
  ③ excel_sheet_parse        把 Sheet 解析成 [{identity, fields}] 记录元组（不碰库）
  ④ sql_record_preview       逐条预演（与库内现有记录 diff），不写库
  ⑤ sql_record_commit        用户确认后落库（同一批传相同 batch_id 聚合报告）
  ⑥ import_report_get        查批次写入结果/失败明细
  辅助：sql_record_flag       单字段打标签（bool/枚举），自带 dry_run，可跳过③④

目标表由 table 参数选择：enterprise（企业）/ inspection（巡查）/ material（材料）。
安全：写库仅限上述 commit/flag 两个工具；preview/parse/plan 均不写库。
报告保存在进程内存，最近 20 批，容器重启清空。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from app.services.svc_Import import import_parser as parser
from app.services.svc_Import.import_writer import (
    enterprise_writer,
    material_writer,
    inspection_writer,
    TableWriter,
)

# ── writer 注册表：table 名 -> writer 实例 ────────────────────
WRITERS: dict[str, TableWriter] = {
    "enterprise": enterprise_writer,
    "material": material_writer,
    "inspection": inspection_writer,
}


def _get_writer(table: str) -> TableWriter | None:
    w = WRITERS.get(table)
    if w is None:
        return None
    return w


# 进程内报告存储：batch_id -> 批次聚合报告（保留最近 20 批）
_REPORTS: dict[str, dict] = {}
_REPORT_ORDER: list[str] = []
_MAX_REPORTS = 20


def _dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _new_batch_id(prefix: str) -> str:
    """唯一批次号：前缀-时间戳-随机短串，避免同秒多记录互相覆盖。"""
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _summarize(results: list[dict]) -> dict:
    """WriteResult.to_dict() 列表 -> 计数 + 失败明细 + 重要修改摘要。"""
    counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    failures = []
    important_changes = []
    for r in results:
        action = r.get("action", "failed")
        counts[action] = counts.get(action, 0) + 1
        if action == "failed":
            failures.append({"name": r.get("name", ""), "error": r.get("message", "")})
        if r.get("important_changes"):
            important_changes.append({
                "name": r.get("name", ""),
                "id": r.get("id"),
                "fields": r["important_changes"],
            })
    return {
        "counts": counts,
        "total": len(results),
        "failures": failures,
        "important_changes": important_changes,
    }


def _append_report(batch_id: str, table: str, result_dict: dict) -> dict:
    """把一条写入结果追加到批次报告（同 batch_id 聚合，不覆盖）。

    返回聚合后的完整批次报告。
    """
    existing = _REPORTS.get(batch_id)
    results = list(existing["results"]) if existing else []
    results.append(result_dict)
    doc = {
        "batch_id": batch_id,
        "table": table,
        "created_at": (
            existing["created_at"] if existing
            else datetime.now().isoformat(timespec="seconds")
        ),
        "results": results,
    }
    doc.update(_summarize(results))
    if existing is None:
        _REPORT_ORDER.append(batch_id)
        if len(_REPORT_ORDER) > _MAX_REPORTS:
            old = _REPORT_ORDER.pop(0)
            _REPORTS.pop(old, None)
    _REPORTS[batch_id] = doc
    return doc


def register_import_tools(mcp) -> None:
    """在给定 FastMCP 实例上注册 Excel 导入工具。"""

    # ══════════════════════════════════════════════════════════════
    # 辅助工具：Excel 探索
    # ══════════════════════════════════════════════════════════════

    @mcp.tool()
    async def excel_sheets_list(file_path: str = parser.DEFAULT_XLSX_PATH) -> str:
        """【Excel导入·①探查】列出工作簿全部 Sheet 名（只读 Sheet 名，不读数据行）。

        何时用：Excel 导入第 1 步——不知道文件里有哪些表时先调它，再决定导哪张。
        输入字段：
            file_path: 服务器上 Excel 文件绝对路径（不传用默认台账路径）。
        返回字段（JSON）：{sheets: [Sheet 名, ...]} 或 {error: ...}
        边界：只读 Excel 结构，不碰数据库；文件不存在/格式错返回 error。
        """
        try:
            return _dumps({"sheets": parser.list_sheets(file_path)})
        except parser.ParserError as e:
            return _dumps({"error": str(e)})

    @mcp.tool()
    async def excel_sheet_inspect(
        sheet_name: str,
        table: str = "enterprise",
        file_path: str = parser.DEFAULT_XLSX_PATH,
    ) -> str:
        """【Excel导入·②单表探查】探测一个 Sheet 的结构：表头行、列名、样本行、建议列映射。

        何时用：只导单个 Sheet 时的第 2 步——拿 suggested_col_map，核对后传给 excel_sheet_parse。
        何时不要用：工作簿有很多 Sheet、想一次看清全部该导什么 → 改用 excel_workbook_plan。
        输入字段：
            sheet_name: 要探测的 Sheet 名。
            table: 目标表 enterprise（默认）/ inspection / material；
                决定按哪张表的字段规则生成列映射建议。
            file_path: Excel 文件绝对路径。
        返回字段（JSON）：
            - 常规 Sheet：header_row / columns / samples（前 3 行）/
              suggested_col_map {Excel列名: 目标字段} / suggested_tag
            - 无表头 Sheet（如月度隐患表）：headerless=true + positional_col_map（按列位置映射）
        边界：只读 Excel，不碰库；建议映射仅供核对，不会自动写入。
        """
        writer = _get_writer(table)
        if writer is None:
            return _dumps({"error": f"未知表名：{table}，可用：{', '.join(WRITERS)}"})

        # 无表头 Sheet（如月度隐患表）：首行即数据，按列字母位置探测
        is_headerless = getattr(writer, "is_headerless", None)
        positional_map = getattr(writer, "positional_col_map", None)
        if is_headerless and positional_map and is_headerless(sheet_name):
            try:
                wb, ws = parser.load_sheet(file_path, sheet_name)
                try:
                    rows = parser.read_rows_by_column(ws, 0)
                finally:
                    wb.close()
            except parser.ParserError as e:
                return _dumps({"error": str(e)})
            return _dumps({
                "sheet": sheet_name,
                "dims": f"{ws.max_row} 行 × {ws.max_column} 列",
                "headerless": True,
                "note": "该 Sheet 无表头，首行即数据；parse 时按列字母位置自动映射，无需传 col_map。",
                "positional_col_map": positional_map,
                "columns": [f"{col}（→{tgt}）" for col, tgt in positional_map.items()],
                "sample_rows": rows[:3],
                "data_row_count": len(rows),
            })

        try:
            return _dumps(parser.inspect_sheet(
                file_path,
                sheet_name,
                field_specs=writer.field_specs,
                col_map_rules=writer.col_map_rules,
                sheet_tag_rules=writer.sheet_tag_rules,
            ))
        except parser.ParserError as e:
            return _dumps({"error": str(e)})

    @mcp.tool()
    async def excel_workbook_plan(
        table: str = "enterprise",
        file_path: str = parser.DEFAULT_XLSX_PATH,
    ) -> str:
        """【Excel导入·②全簿规划】扫描工作簿所有 Sheet，一次给出每个 Sheet 的导入建议（推荐入口）。

        何时用：多 Sheet 台账导入的第 2 步——不确定各 Sheet 该不该导、怎么导时先出全簿计划。
        与 excel_sheet_inspect 的选择：只导一张明确的 Sheet 用 inspect；多 Sheet 或不确定用本工具。
        输入字段：
            table: 目标表 enterprise（默认）/ inspection / material。
            file_path: Excel 文件绝对路径。
        返回字段（JSON）：
            - summary: 各建议类型计数
            - plan[]: 每 Sheet 含 dims / suggested_col_map / match_stats /
              ambiguous（低置信改名候选，必须人工确认）/ recommendation / reason
            - recommendation ∈ mapped（可直接导）/ tag_only（仅打标签）/ review（需人工核对）/ skip（忽略）
        后续编排：按 recommendation 分别调 excel_sheet_parse → preview → commit；
            review 项必须先与用户确认；所有 commit 传同一个 batch_id 聚合报告。
        边界：只读 Excel + 只读一次库内企业名索引做匹配率分析，不写库。
        """
        writer = _get_writer(table)
        if writer is None:
            return _dumps({"error": f"未知表名：{table}，可用：{', '.join(WRITERS)}"})

        # 库内实体名称索引 + 业务启发式词表均由 writer 声明（本工具不按表名硬编码）
        db_index = await writer.workbook_name_index()
        hints = writer.workbook_hints or None

        try:
            plan = parser.workbook_plan(
                file_path,
                field_specs=writer.field_specs,
                col_map_rules=writer.col_map_rules,
                sheet_tag_rules=writer.sheet_tag_rules,
                db_index=db_index,
                hints=hints,
            )
        except parser.ParserError as e:
            return _dumps({"error": str(e)})

        summary: dict[str, int] = {}
        for e in plan:
            rec = e.get("recommendation", "review")
            summary[rec] = summary.get(rec, 0) + 1

        return _dumps({
            "file": file_path,
            "table": table,
            "db_entity_count": len(db_index["names"]) if db_index else 0,
            "hints_enabled": hints is not None,
            "sheet_count": len(plan),
            "summary": summary,
            "plan": plan,
        })

    # ══════════════════════════════════════════════════════════════
    # 工具1：L4 解析+聚合
    # ══════════════════════════════════════════════════════════════

    @mcp.tool()
    async def excel_sheet_parse(
        sheet_name: str,
        col_map: dict | None = None,
        table: str = "enterprise",
        limit: int = 0,
        header_row: int = 0,
        file_path: str = parser.DEFAULT_XLSX_PATH,
    ) -> str:
        """【Excel导入·③解析】把一个 Sheet 解析、规范化并按主键聚合成记录元组（不碰数据库）。

        何时用：导入第 3 步，在 inspect/plan 拿到列映射之后；产物 records 供后续 preview/commit。
        输入字段：
            sheet_name: Sheet 名。
            col_map: {Excel列名: 目标字段}，来自 inspect 的 suggested_col_map（经核对）；
                目标值可写字段名，或 "extra:自定义键名" 落入扩展字段。
                无表头 Sheet 可不传，自动用按列位置映射。
            table: enterprise（默认）/ inspection / material。
            limit: 只处理前 N 行（试跑用），0=全部。
            header_row: 表头行号（1-based），0=自动定位。
            file_path: Excel 文件绝对路径。
        返回字段（JSON）：
            - records[]: {identity: {code/name...}, fields: {字段: 值}} 一条记录一个元组
            - sheet_tag（Sheet 名暗示的标签）/ headerless
            - aggregated_count（聚合后记录数）/ raw_row_count / parse_failures[]（失败行及原因）
        边界：纯解析与聚合，完全不碰数据库；同一企业跨合并单元格的多行自动归属聚合。
        """
        writer = _get_writer(table)
        if writer is None:
            return _dumps({"error": f"未知表名：{table}，可用：{', '.join(WRITERS)}"})

        # 无表头 Sheet（如月度隐患表）：按列字母位置读取 + 用 writer 位置映射
        is_headerless = getattr(writer, "is_headerless", None)
        positional_map = getattr(writer, "positional_col_map", None)
        headerless = bool(
            is_headerless and positional_map and is_headerless(sheet_name)
        )
        effective_col_map = col_map or (positional_map if headerless else None)
        if not effective_col_map:
            return _dumps({"error": "col_map 不能为空（先用 excel_sheet_inspect 获取建议映射）"})

        try:
            wb, ws = parser.load_sheet(file_path, sheet_name)
            try:
                if headerless:
                    rows = parser.read_rows_by_column(ws, header_row or 0)
                else:
                    rows = parser.read_rows(ws, header_row or None)
            finally:
                wb.close()
        except parser.ParserError as e:
            return _dumps({"error": str(e)})

        if limit and limit > 0:
            rows = rows[:limit]

        # 解析 + 规范化（从 field_specs 推导规范化器）
        # 失败行的展示标识：优先取映射到 enterprise_name 的列值，
        # 其次 writer workbook_hints 实体关键词，最后该行第一个非空值
        name_col = next(
            (c for c, t in effective_col_map.items() if t == "enterprise_name"), ""
        )
        entity_keywords = writer.workbook_hints.get("entity_keywords", ())
        skeletons: list[dict] = []
        parse_failures: list[dict] = []
        for i, raw in enumerate(rows, start=1):
            skeleton, errors = parser.normalize_row(raw, effective_col_map, writer.field_specs)
            if errors:
                name_hint = ""
                if name_col:
                    name_hint = str(raw.get(name_col) or "")
                if not name_hint:
                    for kw in entity_keywords:
                        v = raw.get(kw)
                        if v:
                            name_hint = str(v)
                            break
                if not name_hint:
                    name_hint = str(next((v for v in raw.values() if v), ""))
                parse_failures.append(
                    {"row": i, "name": name_hint, "error": "；".join(errors)}
                )
                continue
            if skeleton:
                skeletons.append(skeleton)

        # writer 拆分/派生钩子（如隐患表：一行多隐患拆成一行一隐患 + 批次/状态/期限派生）
        expand = getattr(writer, "expand_skeletons", None)
        if expand is not None:
            skeletons = expand(skeletons, sheet_name)

        # 按主键聚合
        sheet_tag = parser.suggest_sheet_tags(sheet_name, writer.sheet_tag_rules)
        aggregated = parser.aggregate_by_key(
            skeletons, writer.identity_fields, sheet_tag
        )

        return _dumps({
            "headerless": headerless,
            "records": [
                {"identity": ident, "fields": fields}
                for ident, fields in aggregated
            ],
            "sheet_tag": sheet_tag,
            "parse_failures": parse_failures,
            "aggregated_count": len(aggregated),
            "raw_row_count": len(rows),
        })

    # ══════════════════════════════════════════════════════════════
    # 工具2：L3+L2 preview（不写库）
    # ══════════════════════════════════════════════════════════════

    @mcp.tool()
    async def sql_record_preview(
        table: str,
        identity: dict,
        fields: dict,
        override: bool = False,
    ) -> str:
        """【Excel导入·④预演】对单条记录做"若现在写入会怎样"的 diff 预览，不写库。

        何时用：导入第 4 步，每条 parse 出的记录先经本工具预演，把结果给用户确认后再 commit。
        输入字段：
            table: enterprise / inspection / material。
            identity: 身份键，如 {"code": "91..."} 或 {"name": "企业A"}（决定新建还是更新已有记录）。
            fields: 数据字段，如 {"industry": "化工", "has_dormitory": true}。
            override: true=覆盖已有标量值；false（默认）=合并模式，只补空/数组追加。
        返回字段（JSON）：
            - action: created（库中无此记录）/ updated / skipped / failed
            - id / name / matched_field（靠哪个身份键命中）
            - changes / important_changes / field_details[]（每字段 current→incoming→merged）
            - errors[]
        边界：只读库做 diff，绝不写入；也不做权限判断以外的任何副作用。
        """
        writer = _get_writer(table)
        if writer is None:
            return _dumps({"error": f"未知表名：{table}，可用：{', '.join(WRITERS)}"})
        result = await writer.preview(identity, fields, override=override)
        return _dumps(result.to_dict())

    # ══════════════════════════════════════════════════════════════
    # 工具3：L1 commit（写库）
    # ══════════════════════════════════════════════════════════════

    @mcp.tool()
    async def sql_record_commit(
        table: str,
        identity: dict,
        fields: dict,
        override: bool = False,
        batch_id: str = "",
    ) -> str:
        """【Excel导入·⑤落库】把一条记录真正写入业务表（会写库并记审计日志），需在 preview 确认后调用。

        何时用：用户确认 preview 的 changes 无误后；批量导入时对每条记录调用。
        与其它写库工具的区别（勿混用）：
          - 本工具：Excel 导入链路，入参是 parse 产出的 identity+fields，支持新建记录
          - sql_record_flag：只改一个 bool/枚举标签字段，适合批量打标
          - sql_enterprise_fields_commit：从知识库文档补单个企业空字段（另一工具组，不可新建）
        输入字段：
            table: enterprise / inspection / material。
            identity: 身份键 dict（同 preview）。
            fields: 数据字段 dict（同 preview）。
            override: true=覆盖标量；false（默认）=合并只补空/数组追加。
            batch_id: 批次号。同一批多条记录传相同值 → 聚合成一份报告；不传则每条独立成批。
        返回字段（JSON，批次聚合报告）：
            - batch_id / counts: {created, updated, skipped, failed} / total
            - failures[]（失败名+原因）/ important_changes[] / results[]（每条明细）
        边界：真实写库 + 审计日志；巡查表有状态流转等服务端校验，非法写入会计入 failed。
        """
        writer = _get_writer(table)
        if writer is None:
            return _dumps({"error": f"未知表名：{table}，可用：{', '.join(WRITERS)}"})
        result = await writer.commit(identity, fields, override=override)
        bid = batch_id or _new_batch_id("CMT")
        return _dumps(_append_report(bid, table, result.to_dict()))

    # ══════════════════════════════════════════════════════════════
    # 便捷工具：打标签
    # ══════════════════════════════════════════════════════════════

    @mcp.tool()
    async def sql_record_flag(
        table: str,
        identity: dict,
        field: str,
        value: Any = None,
        dry_run: bool = True,
        batch_id: str = "",
    ) -> str:
        """【Excel导入·打标签】给已有记录改单个 bool/枚举标签字段，预演+落库一步完成（默认只预演）。

        何时用：只改一个标签（如"有宿舍=true"、"重大危险源=构成…"），不想走 parse→preview→commit 全套。
        何时不要用：一次改多个/多种字段 → 用 preview + commit；
            从知识库文档补字段 → 用 sql_enterprise_fields_commit。
        输入字段：
            table: enterprise / inspection / material。
            identity: 身份键 dict，如 {"name": "企业A"}。
            field: 标签字段名，必须是表中已声明的非身份键 bool/枚举字段。
            value: bool 字段不传或传 true/false；枚举字段必须传允许值（越界会被拒）。
            dry_run: true（默认）只预演不写库；用户确认后传 false 落库。
            batch_id: 仅 dry_run=false 时用于聚合报告。
        返回字段（JSON）：dry_run=true 返回预演 diff（同 preview 子集）；
            dry_run=false 返回批次聚合报告（同 commit）。
        边界：dry_run=false 时写库 + 审计日志；身份键字段不可打标签。
        """
        writer = _get_writer(table)
        if writer is None:
            return _dumps({"error": f"未知表名：{table}，可用：{', '.join(WRITERS)}"})

        # 从 field_specs 检查字段是否存在 + 是否枚举
        spec = writer.field_specs.get(field)
        if spec is None:
            return _dumps({"error": f"字段 {field} 未在 {table} 表中声明"})
        if spec.is_identity:
            return _dumps({"error": f"字段 {field} 是身份键，不能打标签"})

        # 规范化 value
        if spec.data_type == "value":
            tag_value = parser.parse_bool(value) if value is not None else True
            if tag_value is None:
                return _dumps({"error": f"value '{value}' 无法识别为是/否"})
        elif spec.allowed:
            tag_value = parser.parse_enum(value, spec.allowed) if value is not None else None
            if tag_value is None:
                return _dumps({"error": f"value '{value}' 不在 {'/'.join(spec.allowed)}"})
        else:
            tag_value = value

        if dry_run:
            result = await writer.preview(identity, {field: tag_value})
            return _dumps({
                "action": result.action,
                "id": result.obj_id,
                "name": result.name,
                "matched_field": result.matched_field,
                "changes": result.changes,
                "important_changes": result.important_changes,
                "errors": result.errors,
                "field_details": result.field_details,
            })
        else:
            result = await writer.flag(identity, field, tag_value)
            bid = batch_id or _new_batch_id("FLG")
            return _dumps(_append_report(bid, table, result.to_dict()))

    @mcp.tool()
    async def import_report_get(batch_id: str = "") -> str:
        """【Excel导入·⑥报告】查询导入/打标签批次的写入报告（进程内存保留最近 20 批）。

        何时用：commit/flag(dry_run=false) 之后查结果、核对失败明细、回看最近导入了哪些批次。
        输入字段：
            batch_id: commit/flag 返回的批号。传值→该批完整报告；不传→最近 20 批摘要列表。
        返回字段（JSON）：
            - 传 batch_id：batch_id / table / counts / failures[] / important_changes[] / results[]
            - 不传：recent_batches[]: {batch_id, table, created_at, total, counts}
        边界：只读进程内存，不碰 Excel/SQL；批次号重启即失效。
        """
        if batch_id:
            doc = _REPORTS.get(batch_id)
            if doc is None:
                return _dumps({"error": f"批次 {batch_id} 不存在（报告仅保留最近 {_MAX_REPORTS} 批）"})
            return _dumps(doc)
        if not _REPORT_ORDER:
            return _dumps({"error": "本进程还没有导入报告"})
        recent = [
            {
                "batch_id": bid,
                "table": _REPORTS[bid]["table"],
                "created_at": _REPORTS[bid]["created_at"],
                "total": _REPORTS[bid]["total"],
                "counts": _REPORTS[bid]["counts"],
            }
            for bid in reversed(_REPORT_ORDER)
        ]
        return _dumps({"recent_batches": recent})
