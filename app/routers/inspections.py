"""巡查台账端点（inspection_record，一巡查多隐患）：列表 / 统计 / 批次 / CRUD。"""

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel

router = APIRouter(prefix="/inspection-records", tags=["巡查台账"])


def _parse_date(value: str | None):
    """把 'YYYY-MM-DD' 字符串转成 date；空值返回 None。"""
    if not value:
        return None
    from datetime import date as _date
    try:
        return _date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"日期格式无效: {value}")


class InspectionHazardItem(BaseModel):
    """新建巡查单的多隐患条目（等级/类别/描述逐条录入）。"""
    hazard_level: str = "low"
    hazard_category: str = ""
    hazard_description: str


class InspectionCreateRequest(BaseModel):
    """新建巡查记录请求体（前端字段名已在 api 层做映射）。

    inspection_batch 可选：
      - 不传 → 后端生成新批次号（一次新的巡查）
      - 传入 → 复用已有批次（加入同一次巡查）

    hazards 可选：多条隐患一次录入；不传时回退旧单条字段。
    """
    enterprise_id: int
    inspection_date: str  # YYYY-MM-DD
    inspectors: str = ""  # 巡查人原文（顿号分隔多人，可自由键入）
    inspector_user_id: int | None = None  # 可选：匹配到的员工档案
    title: str = ""  # 可留空
    hazards: list[InspectionHazardItem] | None = None
    hazard_level: str = "low"          # 旧单条入参（hazards 为空时生效）
    hazard_category: str = ""
    hazard_description: str = ""
    rect_responsible_user_id: int | None = None
    rect_responsible_name: str = ""  # 纯文本责任人（企业安全员/负责人等非员工档案时）
    rect_deadline: str | None = None  # YYYY-MM-DD
    inspection_batch: str | None = None


class InspectionUpdateRequest(BaseModel):
    """更新巡查记录（用于状态流转 + 字段修改 + 批量修改）。"""
    title: str | None = None
    location: str | None = None
    inspection_date: str | None = None
    inspection_batch: str | None = None
    inspector_user_id: int | None = None
    inspectors: str | None = None
    hazard_level: str | None = None
    hazard_category: str | None = None
    hazard_description: str | None = None
    rect_responsible_user_id: int | None = None
    rect_responsible_name: str | None = None
    rect_deadline: str | None = None
    review_date: str | None = None        # 复查日期 YYYY-MM-DD（完成整改时键入）
    rect_completed_at: str | None = None  # 整改完成时间 YYYY-MM-DD（完成整改时键入，精确到日）
    notice_number: str | None = None      # 交办单编号（同批次共享，服务层批量同步）
    status: str | None = None


@router.get("")
async def list_inspection_records_endpoint(
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    status: str = "",
    enterprise_id: str = "",
    inspection_batch: str = "",
):
    """巡查记录列表（分页 + 关键词/状态/企业/批次筛选，附带企业名/巡查人名）。

    enterprise_id 用 str 接收：前端筛选栏未选企业时会传空串，
    声明成 int|None 会被 FastAPI 判为 422，空串在此归一为 None。
    inspection_batch 非空时精确匹配批次（一次到点巡查可能覆盖多企业）。
    """
    from app.services.svc_Ledger.inspection_service import list_records, InspectionError

    try:
        items, total = await list_records(
            page=page,
            page_size=page_size,
            keyword=keyword or None,
            status=status or None,
            enterprise_id=int(enterprise_id) if enterprise_id and enterprise_id.isdigit() else None,
            inspection_batch=inspection_batch or None,
        )
    except InspectionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": {"total": total, "items": items}}


@router.get("/stats")
async def inspection_stats_endpoint():
    """巡查台账统计（总数、各状态数、逾期数、隐患等级分布）。"""
    from app.services.svc_Ledger.inspection_service import get_stats

    return {"success": True, "data": await get_stats()}


@router.get("/batches")
async def list_inspection_batches_endpoint():
    """已有巡查批次列表（供新建时选择"加入同一次巡查"）。

    返回 [{batch, title, inspection_date, hazard_count}]，按批次聚合。
    """
    from app.services.svc_Ledger.inspection_service import list_batches
    return {"success": True, "data": await list_batches()}


@router.get("/unmatched-enterprises")
async def list_unmatched_enterprises_endpoint():
    """未关联企业档案的记录分组（兼容保留）。

    B 方案收口后：enterprise_name/enterprise_code 冗余列已删除、导入时
    匹配不到自动建档，enterprise_id 100% 关联 —— 恒返回空列表。
    """
    return {"success": True, "data": []}


class RelinkEnterpriseRequest(BaseModel):
    """（兼容保留）把某个企业名下的隐患记录统一关联到目标企业档案。"""
    enterprise_name: str
    enterprise_id: int
    inspection_batch: str | None = None


@router.post("/relink-enterprise")
async def relink_enterprise_endpoint(body: RelinkEnterpriseRequest):
    """手动关联（兼容保留，恒为 no-op）。

    B 方案收口后 inspection_record 不再存企业名快照、记录 100% 关联
    enterprise_id，"按名称回填指针"的场景已消失。接口保留以兼容前端，
    直接返回成功（updated=0）。
    """
    return {
        "success": True,
        "data": {"updated": 0, "enterprise_name": ""},
        "message": "巡查记录已全部关联企业档案，无需手动关联",
    }


@router.get("/meta/filter-fields")
async def inspection_filter_fields_endpoint():
    """巡查表筛选字段元数据（驱动前端表头筛选 UI，与企业表同一套协议）。"""
    from app.services.svc_Ledger.inspection_service import get_inspection_filter_fields

    return {
        "success": True,
        "data": [f.model_dump() for f in get_inspection_filter_fields()],
    }


class InspectionFilterRequest(BaseModel):
    """复合条件查询（结构与 EnterpriseFilterRequest 一致，见 filter_service）。"""
    filter: dict
    page: int = 1
    page_size: int = 20


@router.post("/query")
async def query_inspection_records_endpoint(body: InspectionFilterRequest):
    """复合条件分页查询巡查记录（与 POST /enterprises/query 同一套筛选协议）。"""
    from app.services.svc_Common.filter_service import FilterError, FilterGroup
    from app.services.svc_Ledger.inspection_service import list_records_with_filter

    try:
        root = FilterGroup.model_validate(body.filter)
        items, total = await list_records_with_filter(
            root, page=body.page, page_size=body.page_size
        )
    except FilterError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"success": True, "data": {"total": total, "items": items}}


@router.get("/{record_id}")
async def get_inspection_record_endpoint(record_id: int):
    """巡查记录详情（附带外键展示名）。"""
    from app.services.svc_Ledger.inspection_service import get_record

    r = await get_record(record_id)
    if not r:
        return {"success": False, "data": None, "message": "记录不存在"}
    return {"success": True, "data": r}


@router.post("")
async def create_inspection_record_endpoint(
    body: InspectionCreateRequest, request: Request
):
    """新建巡查记录（自动归属当日批次；hazards 支持一次多条隐患）。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.inspection_service import create_record, get_record, InspectionError

    try:
        created = await create_record(
            enterprise_id=body.enterprise_id,
            inspection_date=_parse_date(body.inspection_date),
            inspectors=body.inspectors,
            inspector_user_id=body.inspector_user_id,
            title=body.title,
            hazards=[h.dict() for h in body.hazards] if body.hazards else None,
            hazard_level=body.hazard_level,
            hazard_category=body.hazard_category,
            hazard_description=body.hazard_description,
            rect_responsible_user_id=body.rect_responsible_user_id,
            rect_responsible_name=body.rect_responsible_name,
            rect_deadline=_parse_date(body.rect_deadline),
            inspection_batch=body.inspection_batch,
        )
    except InspectionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_action_async(
        action="inspection.create",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="inspection",
        resource_id=str(created[0].id),
        request_payload={
            "batch": created[0].inspection_batch,
            "title": body.title,
            "hazard_count": len(created),
        },
    )
    items = [await get_record(r.id) for r in created]
    return {"success": True, "data": items if len(items) > 1 else items[0]}


@router.put("/{record_id}")
async def update_inspection_record_endpoint(
    record_id: int, body: InspectionUpdateRequest, request: Request
):
    """更新巡查记录（含状态流转校验：open→rectifying→closed）。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.inspection_service import update_record, InspectionError

    try:
        r = await update_record(
            record_id,
            title=body.title,
            location=body.location,
            inspection_date=_parse_date(body.inspection_date) if body.inspection_date else None,
            inspection_batch=body.inspection_batch,
            inspector_user_id=body.inspector_user_id,
            inspectors=body.inspectors,
            hazard_level=body.hazard_level,
            hazard_category=body.hazard_category,
            hazard_description=body.hazard_description,
            rect_responsible_user_id=body.rect_responsible_user_id,
            rect_responsible_name=body.rect_responsible_name,
            rect_deadline=_parse_date(body.rect_deadline) if body.rect_deadline else None,
            review_date=_parse_date(body.review_date) if body.review_date else None,
            rect_completed_at=_parse_date(body.rect_completed_at) if body.rect_completed_at else None,
            notice_number=body.notice_number,
            status=body.status,
        )
    except InspectionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not r:
        return {"success": False, "data": None, "message": "记录不存在"}

    if body.status:
        log_action_async(
            action=f"inspection.status.{body.status}",
            staff_id=getattr(request.state, "user", None) and request.state.user.id,
            resource_type="inspection",
            resource_id=str(record_id),
        )
    return {"success": True, "data": r}


@router.delete("/{record_id}")
async def delete_inspection_record_endpoint(record_id: int, request: Request):
    """删除巡查记录（仅已闭环可删）。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.inspection_service import delete_record, InspectionError

    try:
        ok = await delete_record(record_id)
    except InspectionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        return {"success": False, "data": None, "message": "记录不存在"}
    log_action_async(
        action="inspection.delete",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="inspection",
        resource_id=str(record_id),
    )
    return {"success": True, "message": "删除成功"}


@router.post("/import")
async def import_inspection_excel(file: UploadFile = File(...)):
    """导入总隐患汇总表 Excel（支持月度无表头 sheet + 年度有表头 sheet）。

    自动按 sheet 名判断格式，逐 sheet 解析→拆分多隐患→匹配企业→落库。
    匹配不到企业的行按名称自动建档（数据源锁死 Enterprise 主表，
    enterprise_id 100% 关联）。
    """
    import os
    import tempfile
    import openpyxl
    from app.services.svc_Import import import_parser as parser
    from app.services.svc_Import.import_writer import inspection_writer as W

    suffix = os.path.splitext(file.filename or ".xlsx")[1] or ".xlsx"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    content = await file.read()
    tmp.write(content)
    tmp.close()

    try:
        wb = openpyxl.load_workbook(tmp.name, data_only=True)
        results = []
        total = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
        unmatched_all = []

        for sheet_name in wb.sheetnames:
            if "分类表" in sheet_name or "统计" in sheet_name:
                continue

            ws = wb[sheet_name]
            headerless = W.is_headerless(sheet_name)

            if headerless:
                rows = parser.read_rows_by_column(ws, 0)
                col_map = dict(W.positional_col_map)
            else:
                hr = parser.locate_header(ws)
                if not hr:
                    results.append({"sheet": sheet_name, "error": "找不到表头行"})
                    continue
                headers = [
                    str(ws.cell(row=hr, column=c).value or "").strip()
                    for c in range(1, ws.max_column + 1)
                ]
                col_map = parser.suggest_col_map(headers, W.col_map_rules)
                rows = parser.read_rows(ws, hr)

            skeletons = []
            parse_failures = []
            for i, raw in enumerate(rows, 1):
                sk, errs = parser.normalize_row(raw, col_map, W.field_specs)
                if errs:
                    parse_failures.append({"row": i, "errors": errs})
                elif sk:
                    skeletons.append(sk)

            expanded = W.expand_skeletons(skeletons, sheet_name)
            # 聚合键用解析层身份（含 enterprise_name，Excel 原文可用）；
            # commit 内部把 name 解析为 enterprise_id 后再按 DB 身份键 resolve
            agg = parser.aggregate_by_key(
                expanded, ("inspection_batch", "enterprise_name", "hazard_index"), None
            )

            sc = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
            sheet_unmatched = []
            failures = []
            for ident, fields in agg:
                name = ident.get("enterprise_name") or ""
                # commit 前预检查企业匹配（_resolve_enterprise 有 name cache，不重复查库）
                if name:
                    ent = await W._resolve_enterprise(name)
                    if not ent:
                        sheet_unmatched.append(name)
                try:
                    wr = await W.commit(ident, fields)
                    if wr.action in sc:
                        sc[wr.action] += 1
                    else:
                        sc["failed"] += 1
                except Exception as e:
                    sc["failed"] += 1
                    failures.append({"enterprise": name, "error": str(e)[:200]})

            for k in total:
                total[k] += sc[k]
            unmatched_all.extend(sheet_unmatched)

            results.append({
                "sheet": sheet_name,
                "headerless": headerless,
                "raw_rows": len(rows),
                "expanded_hazards": len(expanded),
                "counts": sc,
                "parse_failures": parse_failures[:10],
                "unmatched_enterprises": sorted(set(sheet_unmatched)),
                "commit_failures": failures[:10],
            })

        wb.close()
        # 未匹配企业去重 + 疑似候选提示（仅提示，不自动回填）
        unmatched_detail = []
        for uname in sorted(set(unmatched_all)):
            sug = await W.suggest_enterprise(uname)
            unmatched_detail.append(
                {"name": uname, "suggestion": sug["name"] if sug else None,
                 "score": sug["score"] if sug else None}
            )
        return {
            "success": True,
            "data": {
                "total_counts": total,
                "sheets": results,
                "unmatched_enterprises": unmatched_detail,
            },
        }
    finally:
        os.unlink(tmp.name)
