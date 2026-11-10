"""厂中厂专项整治端点（factory_in_factory，出租方-承租方关系）：列表 / 批次 / CRUD / 复合筛选。"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

router = APIRouter(prefix="/factory-in-factory", tags=["厂中厂台账"])


def _parse_date(value: str | None):
    """把 'YYYY-MM-DD' 字符串转成 date；空值返回 None。"""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class FactoryInFactoryCreateRequest(BaseModel):
    """新建厂中厂记录请求体。"""
    inspection_batch: str
    lessor_name: str = ""
    tenant_name: str = ""
    lessor_enterprise_id: int | None = None
    tenant_enterprise_id: int | None = None
    region: str = ""
    address: str = ""
    lessor_contact_person: str = ""
    lessor_contact_phone: str = ""
    lessor_use_area: str = ""
    lessor_hazard_description: str = ""
    lessor_rect_status: str = ""
    tenant_area: float | None = None
    tenant_contact_person: str = ""
    tenant_contact_phone: str = ""
    agreement_signed: bool | None = None
    tenant_hazard_description: str = ""
    tenant_rect_status: str = ""
    site_category: str = ""
    inspection_date: str = ""
    inspectors: str = ""
    rect_supervisor: str = ""
    remark: str = ""


class FactoryInFactoryUpdateRequest(BaseModel):
    """更新厂中厂记录请求体（所有字段可选；仅显式传入的字段会被更新）。"""
    inspection_batch: str | None = None
    lessor_name: str | None = None
    tenant_name: str | None = None
    lessor_enterprise_id: int | None = None
    tenant_enterprise_id: int | None = None
    region: str | None = None
    address: str | None = None
    lessor_contact_person: str | None = None
    lessor_contact_phone: str | None = None
    lessor_use_area: str | None = None
    lessor_hazard_description: str | None = None
    lessor_rect_status: str | None = None
    tenant_area: float | None = None
    tenant_contact_person: str | None = None
    tenant_contact_phone: str | None = None
    agreement_signed: bool | None = None
    tenant_hazard_description: str | None = None
    tenant_rect_status: str | None = None
    site_category: str | None = None
    inspection_date: str | None = None
    inspectors: str | None = None
    rect_supervisor: str | None = None
    remark: str | None = None


class FactoryInFactoryFilterRequest(BaseModel):
    """复合条件查询（结构与 InspectionFilterRequest 一致，见 filter_service）。"""
    filter: dict
    page: int = 1
    page_size: int = 20


@router.get("")
async def list_factory_in_factory_endpoint(
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    inspection_batch: Optional[str] = None,
    region: Optional[str] = None,
    lessor_name: Optional[str] = None,
    tenant_name: Optional[str] = None,
    site_category: Optional[str] = None,
    inspection_date: Optional[str] = None,
):
    """厂中厂列表（分页 + 关键词 + 多维度筛选）。"""
    from app.services.svc_Ledger.factory_in_factory_service import list_records

    items, total = await list_records(
        page=page,
        page_size=page_size,
        keyword=keyword or None,
        inspection_batch=inspection_batch,
        region=region,
        lessor_name=lessor_name,
        tenant_name=tenant_name,
        site_category=site_category,
        inspection_date=_parse_date(inspection_date),
    )
    return {"success": True, "data": {"total": total, "items": items}}


@router.get("/batches")
async def list_factory_in_factory_batches_endpoint():
    """全部 inspection_batch 去重列表（前端下拉用）。"""
    from app.services.svc_Ledger.factory_in_factory_service import list_batches

    return {"success": True, "data": await list_batches()}


@router.get("/meta/filter-fields")
async def factory_in_factory_filter_fields_endpoint():
    """厂中厂表筛选字段元数据（驱动前端表头筛选 UI，与企业表/巡查表同一套协议）。"""
    from app.services.svc_Ledger.factory_in_factory_service import get_factory_in_factory_filter_fields

    return {
        "success": True,
        "data": [f.model_dump() for f in get_factory_in_factory_filter_fields()],
    }


@router.post("/query")
async def query_factory_in_factory_endpoint(body: FactoryInFactoryFilterRequest):
    """复合条件分页查询厂中厂记录（与 POST /enterprises/query 同一套筛选协议）。"""
    from app.services.svc_Ledger.factory_in_factory_service import list_records_with_filter
    from app.services.svc_Common.filter_service import FilterError, FilterGroup

    try:
        root = FilterGroup.model_validate(body.filter)
        items, total = await list_records_with_filter(
            root, page=body.page, page_size=body.page_size
        )
    except FilterError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"success": True, "data": {"total": total, "items": items}}


@router.post("/import")
async def import_factory_in_factory_excel(file: UploadFile = File(...), request: Request = None):
    """导入厂中厂消防安全专项整治责任清单 Excel。

    单/多 Sheet 解析（承租单位列竖向合并单元格界定记录边界）→ 按名称匹配
    出租方/承租方企业主表回填 *_enterprise_id → 落库。匹配不到企业的记录仍
    保留名称，*_enterprise_id 留空（不自动建档，可在企业页补登后重导）。
    重复导入按 (inspection_batch, tenant_name) 幂等：已存在 → 更新，不重复建档。
    """
    import os
    import tempfile
    import openpyxl
    from app.services.svc_Import.import_writer import factory_in_factory_writer as W

    suffix = os.path.splitext(file.filename or ".xlsx")[1] or ".xlsx"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    content = await file.read()
    tmp.write(content)
    tmp.close()

    try:
        wb = openpyxl.load_workbook(tmp.name, data_only=True)
        total = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
        unmatched_lessors: list[str] = []
        unmatched_tenants: list[str] = []
        failures: list[dict] = []
        raw_records = 0

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            records = W.parse_sheet(ws)
            raw_records += len(records)
            for ident, fields in records:
                tname = ident.get("tenant_name") or ""
                lname = fields.get("lessor_name") or ""
                # 预检查企业匹配（_resolve_enterprise_id 有名称缓存，不重复查库）
                if lname and await W._resolve_enterprise_id(lname) is None:
                    unmatched_lessors.append(lname)
                if tname and await W._resolve_enterprise_id(tname) is None:
                    unmatched_tenants.append(tname)
                try:
                    wr = await W.commit(ident, fields)
                    if wr.action in total:
                        total[wr.action] += 1
                    else:
                        total["failed"] += 1
                except Exception as e:  # noqa: BLE001
                    total["failed"] += 1
                    failures.append({"tenant": tname, "error": str(e)[:200]})

        wb.close()
        unmatched = sorted(set(unmatched_lessors) | set(unmatched_tenants))

        from app.services.svc_Auth.audit_service import log_action_async
        log_action_async(
            action="factory_in_factory.import",
            staff_id=getattr(request.state, "user", None) and request.state.user.id,
            resource_type="factory_in_factory",
            resource_id="",
            request_payload={
                "filename": file.filename,
                "counts": total,
                "unmatched": len(unmatched),
            },
        )

        return {
            "success": True,
            "data": {
                "total_counts": total,
                "raw_records": raw_records,
                "unmatched_enterprises": unmatched,
                "commit_failures": failures[:10],
            },
        }
    finally:
        os.unlink(tmp.name)


@router.get("/{record_id}")
async def get_factory_in_factory_endpoint(record_id: int):
    """厂中厂记录详情（附带外键展示名）。"""
    from app.services.svc_Ledger.factory_in_factory_service import get_record

    r = await get_record(record_id)
    if not r:
        return {"success": False, "data": None, "message": "记录不存在"}
    return {"success": True, "data": r}


@router.post("")
async def create_factory_in_factory_endpoint(
    body: FactoryInFactoryCreateRequest, request: Request
):
    """新建厂中厂记录。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.factory_in_factory_service import (
        create_record, get_record, FactoryInFactoryError,
    )

    try:
        r = await create_record(
            inspection_batch=body.inspection_batch,
            lessor_name=body.lessor_name,
            tenant_name=body.tenant_name,
            lessor_enterprise_id=body.lessor_enterprise_id,
            tenant_enterprise_id=body.tenant_enterprise_id,
            region=body.region,
            address=body.address,
            lessor_contact_person=body.lessor_contact_person,
            lessor_contact_phone=body.lessor_contact_phone,
            lessor_use_area=body.lessor_use_area,
            lessor_hazard_description=body.lessor_hazard_description,
            lessor_rect_status=body.lessor_rect_status,
            tenant_area=body.tenant_area,
            tenant_contact_person=body.tenant_contact_person,
            tenant_contact_phone=body.tenant_contact_phone,
            agreement_signed=body.agreement_signed,
            tenant_hazard_description=body.tenant_hazard_description,
            tenant_rect_status=body.tenant_rect_status,
            site_category=body.site_category,
            inspection_date=_parse_date(body.inspection_date),
            inspectors=body.inspectors,
            rect_supervisor=body.rect_supervisor,
            remark=body.remark,
        )
    except FactoryInFactoryError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_action_async(
        action="factory_in_factory.create",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="factory_in_factory",
        resource_id=str(r.id),
        request_payload={"batch": r.inspection_batch, "tenant": body.tenant_name},
    )
    return {"success": True, "data": await get_record(r.id)}


@router.put("/{record_id}")
async def update_factory_in_factory_endpoint(
    record_id: int, body: FactoryInFactoryUpdateRequest, request: Request
):
    """更新厂中厂记录（局部更新语义）。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.factory_in_factory_service import (
        update_record, get_record, FactoryInFactoryError,
    )

    try:
        r = await update_record(
            record_id,
            inspection_batch=body.inspection_batch,
            lessor_name=body.lessor_name,
            tenant_name=body.tenant_name,
            lessor_enterprise_id=body.lessor_enterprise_id,
            tenant_enterprise_id=body.tenant_enterprise_id,
            region=body.region,
            address=body.address,
            lessor_contact_person=body.lessor_contact_person,
            lessor_contact_phone=body.lessor_contact_phone,
            lessor_use_area=body.lessor_use_area,
            lessor_hazard_description=body.lessor_hazard_description,
            lessor_rect_status=body.lessor_rect_status,
            tenant_area=body.tenant_area,
            tenant_contact_person=body.tenant_contact_person,
            tenant_contact_phone=body.tenant_contact_phone,
            agreement_signed=body.agreement_signed,
            tenant_hazard_description=body.tenant_hazard_description,
            tenant_rect_status=body.tenant_rect_status,
            site_category=body.site_category,
            inspection_date=_parse_date(body.inspection_date) if body.inspection_date else None,
            inspectors=body.inspectors,
            rect_supervisor=body.rect_supervisor,
            remark=body.remark,
        )
    except FactoryInFactoryError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not r:
        return {"success": False, "data": None, "message": "记录不存在"}

    log_action_async(
        action="factory_in_factory.update",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="factory_in_factory",
        resource_id=str(record_id),
    )
    return {"success": True, "data": await get_record(r.id)}


@router.delete("/{record_id}")
async def delete_factory_in_factory_endpoint(record_id: int, request: Request):
    """删除厂中厂记录。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.factory_in_factory_service import delete_record

    ok = await delete_record(record_id)
    if not ok:
        return {"success": False, "message": "记录不存在"}

    log_action_async(
        action="factory_in_factory.delete",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="factory_in_factory",
        resource_id=str(record_id),
    )
    return {"success": True}
