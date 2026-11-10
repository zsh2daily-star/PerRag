"""危化品使用企业专项整治端点（hazardous_chemical，一行一种危化品）：列表 / CRUD / 复合筛选。"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/hazardous-chemicals", tags=["危化品台账"])


class HazardousChemicalCreateRequest(BaseModel):
    """新建危化品记录请求体。"""
    enterprise_name: str = ""
    enterprise_id: int | None = None
    region: str = ""
    town: str = ""
    industry: str = ""
    scale: str = ""
    hazchem_name: str = ""
    hazchem_state: str = ""
    reserve: str = ""
    max_reserve: str = ""
    annual_usage: str = ""
    usage: str = ""
    storage_method: str = ""
    storage_location: str = ""
    major_hazard: str = ""
    remark: str = ""


class HazardousChemicalUpdateRequest(BaseModel):
    """更新危化品记录请求体（所有字段可选；仅显式传入的字段会被更新）。"""
    enterprise_name: str | None = None
    enterprise_id: int | None = None
    region: str | None = None
    town: str | None = None
    industry: str | None = None
    scale: str | None = None
    hazchem_name: str | None = None
    hazchem_state: str | None = None
    reserve: str | None = None
    max_reserve: str | None = None
    annual_usage: str | None = None
    usage: str | None = None
    storage_method: str | None = None
    storage_location: str | None = None
    major_hazard: str | None = None
    remark: str | None = None


class HazardousChemicalFilterRequest(BaseModel):
    """复合条件查询（结构与 InspectionFilterRequest 一致，见 filter_service）。"""
    filter: dict
    page: int = 1
    page_size: int = 20


@router.get("")
async def list_hazardous_chemicals_endpoint(
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    region: Optional[str] = None,
    enterprise_name: Optional[str] = None,
    hazchem_name: Optional[str] = None,
    hazchem_state: Optional[str] = None,
    major_hazard: Optional[str] = None,
):
    """危化品列表（分页 + 关键词 + 多维度筛选）。"""
    from app.services.svc_Ledger.hazardous_chemical_service import list_records

    items, total = await list_records(
        page=page,
        page_size=page_size,
        keyword=keyword or None,
        region=region,
        enterprise_name=enterprise_name,
        hazchem_name=hazchem_name,
        hazchem_state=hazchem_state,
        major_hazard=major_hazard,
    )
    return {"success": True, "data": {"total": total, "items": items}}


@router.get("/meta/filter-fields")
async def hazardous_chemicals_filter_fields_endpoint():
    """危化品表筛选字段元数据（驱动前端表头筛选 UI，与企业表/巡查表同一套协议）。"""
    from app.services.svc_Ledger.hazardous_chemical_service import get_hazchem_filter_fields

    return {
        "success": True,
        "data": [f.model_dump() for f in get_hazchem_filter_fields()],
    }


@router.post("/query")
async def query_hazardous_chemicals_endpoint(body: HazardousChemicalFilterRequest):
    """复合条件分页查询危化品记录（与 POST /enterprises/query 同一套筛选协议）。"""
    from app.services.svc_Ledger.hazardous_chemical_service import list_records_with_filter
    from app.services.svc_Common.filter_service import FilterError, FilterGroup

    try:
        root = FilterGroup.model_validate(body.filter)
        items, total = await list_records_with_filter(
            root, page=body.page, page_size=body.page_size
        )
    except FilterError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"success": True, "data": {"total": total, "items": items}}


@router.get("/{record_id}")
async def get_hazardous_chemical_endpoint(record_id: int):
    """危化品记录详情（附带外键展示名）。"""
    from app.services.svc_Ledger.hazardous_chemical_service import get_record

    r = await get_record(record_id)
    if not r:
        return {"success": False, "data": None, "message": "记录不存在"}
    return {"success": True, "data": r}


@router.post("")
async def create_hazardous_chemical_endpoint(
    body: HazardousChemicalCreateRequest, request: Request
):
    """新建危化品记录。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.hazardous_chemical_service import (
        create_record, get_record,
    )

    r = await create_record(
        enterprise_name=body.enterprise_name,
        enterprise_id=body.enterprise_id,
        region=body.region,
        town=body.town,
        industry=body.industry,
        scale=body.scale,
        hazchem_name=body.hazchem_name,
        hazchem_state=body.hazchem_state,
        reserve=body.reserve,
        max_reserve=body.max_reserve,
        annual_usage=body.annual_usage,
        usage=body.usage,
        storage_method=body.storage_method,
        storage_location=body.storage_location,
        major_hazard=body.major_hazard,
        remark=body.remark,
    )

    log_action_async(
        action="hazardous_chemical.create",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="hazardous_chemical",
        resource_id=str(r.id),
        request_payload={"enterprise": body.enterprise_name, "hazchem": body.hazchem_name},
    )
    return {"success": True, "data": await get_record(r.id)}


@router.put("/{record_id}")
async def update_hazardous_chemical_endpoint(
    record_id: int, body: HazardousChemicalUpdateRequest, request: Request
):
    """更新危化品记录（局部更新语义）。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.hazardous_chemical_service import (
        update_record, get_record,
    )

    r = await update_record(
        record_id,
        enterprise_name=body.enterprise_name,
        enterprise_id=body.enterprise_id,
        region=body.region,
        town=body.town,
        industry=body.industry,
        scale=body.scale,
        hazchem_name=body.hazchem_name,
        hazchem_state=body.hazchem_state,
        reserve=body.reserve,
        max_reserve=body.max_reserve,
        annual_usage=body.annual_usage,
        usage=body.usage,
        storage_method=body.storage_method,
        storage_location=body.storage_location,
        major_hazard=body.major_hazard,
        remark=body.remark,
    )

    if not r:
        return {"success": False, "data": None, "message": "记录不存在"}

    log_action_async(
        action="hazardous_chemical.update",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="hazardous_chemical",
        resource_id=str(record_id),
    )
    return {"success": True, "data": await get_record(r.id)}


@router.delete("/{record_id}")
async def delete_hazardous_chemical_endpoint(record_id: int, request: Request):
    """删除危化品记录。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.hazardous_chemical_service import delete_record

    ok = await delete_record(record_id)
    if not ok:
        return {"success": False, "message": "记录不存在"}

    log_action_async(
        action="hazardous_chemical.delete",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="hazardous_chemical",
        resource_id=str(record_id),
    )
    return {"success": True}
