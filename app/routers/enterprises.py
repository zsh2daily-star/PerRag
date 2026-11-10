"""企业管理端点（M1 主数据）：列表 / meta / 查询 / CRUD。"""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/enterprises", tags=["企业管理"])


def _enterprise_to_dict(ent) -> dict:
    """Enterprise → JSON（对齐前端契约：含 updated_at、监管扩展字段）。"""
    return {
        "id": ent.id,
        "name": ent.name,
        "code": ent.code,
        "industry": ent.industry or "",
        "region": ent.region or "",
        "address": ent.address or "",
        "legal_person": ent.legal_person or "",
        # 基础信息
        "person_in_charge": ent.person_in_charge or "",
        "safety_officer": ent.safety_officer or "",
        "legal_person_phone": ent.legal_person_phone or "",
        "person_in_charge_phone": ent.person_in_charge_phone or "",
        "safety_officer_phone": ent.safety_officer_phone or "",
        "enterprise_category": ent.enterprise_category or [],
        "asset_attribute": ent.asset_attribute or "",
        # 规模信息
        "enterprise_scale": ent.enterprise_scale or "",
        "employee_count": ent.employee_count,
        "annual_output_value": ent.annual_output_value,
        # 安全信息
        "fire_level": ent.fire_level or "",
        "confined_space": ent.confined_space or [],
        "confined_space_count": ent.confined_space_count or "",
        "confined_space_type": ent.confined_space_type or "",
        "confined_space_workers": ent.confined_space_workers or "",
        "confined_space_hazards": ent.confined_space_hazards or "",
        "confined_space_op_subject": ent.confined_space_op_subject or "",
        "confined_space_op_cycle": ent.confined_space_op_cycle or "",
        "confined_space_fixed_workers": ent.confined_space_fixed_workers or "",
        "hazchem_names": ent.hazchem_names or "",
        "hazchem_op_subject": ent.hazchem_op_subject or "",
        "hazchem_major_hazard": ent.hazchem_major_hazard or "",
        "hazchem_state": ent.hazchem_state or "",
        "hazchem_op_cycle": ent.hazchem_op_cycle or "",
        "hazchem_usage": ent.hazchem_usage or "",
        "hazchem_annual_usage": ent.hazchem_annual_usage or "",
        "hazchem_storage": ent.hazchem_storage or "",
        "hazchem_reserve": ent.hazchem_reserve or "",
        "hazchem_fixed_workers": ent.hazchem_fixed_workers or "",
        "has_natural_gas": bool(ent.has_natural_gas),
        "is_injection_molding": bool(ent.is_injection_molding),
        "has_dormitory": bool(ent.has_dormitory),
        "has_hazardous_chemicals": bool(ent.has_hazardous_chemicals),
        "has_hazardous_waste": bool(ent.has_hazardous_waste),
        "involves_dust_explosion": bool(ent.involves_dust_explosion),
        "is_new_energy_welding": bool(ent.is_new_energy_welding),
        "has_large_storage_tank": bool(ent.has_large_storage_tank),
        # ── 小场所明细 ──
        "venue_status": ent.venue_status or "",
        "staff_count": ent.staff_count or "",
        "venue_area": ent.venue_area or "",
        "capacity": ent.capacity or "",
        "is_multistory": ent.is_multistory,
        "has_business_license": ent.has_business_license,
        "venue_category_major": ent.venue_category_major or "",
        "venue_category_minor": ent.venue_category_minor or "",
        "is_multi_license": ent.is_multi_license,
        "regulatory_dept": ent.regulatory_dept or "",
        "territorial_dept": ent.territorial_dept or "",
        "venue_safety_inspector": ent.venue_safety_inspector or "",
        "venue_fire_instructor": ent.venue_fire_instructor or "",
        "venue_risk_level": ent.venue_risk_level or "",
        "involves_evacuation": ent.involves_evacuation,
        "involves_fire_safety": ent.involves_fire_safety,
        "involves_electrical": ent.involves_electrical,
        "involves_lpg": ent.involves_lpg,
        "involves_pipe_gas": ent.involves_pipe_gas,
        "involves_other_fuel": ent.involves_other_fuel,
        "involves_decoration_work": ent.involves_decoration_work,
        "involves_three_in_one": ent.involves_three_in_one,
        "involves_flammable_storage": ent.involves_flammable_storage,
        # 园区归属复核标记
        "out_of_park": bool(ent.out_of_park),
        "out_of_park_note": ent.out_of_park_note or "",
        # 长尾自定义字段：jsonb dict 原样返回，无则空对象
        "extra": ent.extra or {},
        "created_at": ent.created_at.strftime("%Y-%m-%d %H:%M:%S") if ent.created_at else "",
        "updated_at": ent.updated_at.strftime("%Y-%m-%d %H:%M:%S") if ent.updated_at else "",
    }


class EnterpriseFilterRequest(BaseModel):
    """复合条件查询请求体（POST /enterprises/query 用）。

    filter 是嵌套组树：{type:'group', logic:'AND'|'OR', children:[...]}
    其中 children 元素可能是 leaf（{type:'leaf', field, operator, value}）
    或子 group（递归）。详细结构见 filter_service.FilterNode。
    """
    filter: dict  # 接收原始 dict，service 层用 Pydantic FilterGroup 解析
    page: int = 1
    page_size: int = 20


class EnterpriseCreateRequest(BaseModel):
    """新建企业请求体。"""
    name: str
    code: str  # 18位统一社会信用代码
    # ── 基础信息 ──
    industry: str = ""
    region: str = ""
    address: str = ""
    legal_person: str = ""  # 法人
    person_in_charge: str = ""  # 企业负责人
    safety_officer: str = ""  # 企业安全员
    legal_person_phone: str = ""  # 法人电话
    person_in_charge_phone: str = ""  # 企业负责人电话
    safety_officer_phone: str = ""  # 安全员电话
    enterprise_category: list[str] = []  # 企业类型多标签：劳密/厂中厂出租/厂中厂承租/小场所/其他
    asset_attribute: str = ""  # 资产属性：韩资/台资/其他
    # ── 规模信息 ──
    enterprise_scale: str = ""  # 企业规模：规上/规下
    employee_count: int | None = None  # 职工人数
    annual_output_value: int | None = None  # 年产值（万元）
    # ── 安全信息 ──
    fire_level: str = ""  # 消防重点单位：二级/三级/重点单位
    confined_space: list[str] = []  # 有限空间多标签：地上/地下（可多选）
    confined_space_count: str = ""  # 有限空间数量
    confined_space_type: str = ""  # 有限空间类型（详细）
    confined_space_workers: str = ""  # 有限空间作业人数
    confined_space_hazards: str = ""  # 有限空间危险因素
    confined_space_op_subject: str = ""  # 有限空间作业主体
    confined_space_op_cycle: str = ""  # 有限空间作业周期
    confined_space_fixed_workers: str = ""  # 有限空间作业人员是否固定
    hazchem_names: str = ""  # 危化品名称
    hazchem_op_subject: str = ""  # 作业主体
    hazchem_major_hazard: str = ""  # 是否重大危险源
    hazchem_state: str = ""  # 危化品状态
    hazchem_op_cycle: str = ""  # 作业周期
    hazchem_usage: str = ""  # 危化品用途
    hazchem_annual_usage: str = ""  # 年度使用量
    hazchem_storage: str = ""  # 存储方式
    hazchem_reserve: str = ""  # 危化品储量
    hazchem_fixed_workers: str = ""  # 作业人员是否固定
    has_natural_gas: bool = False  # 天然气
    is_injection_molding: bool = False  # 注塑企业
    has_dormitory: bool = False  # 宿舍
    has_hazardous_chemicals: bool = False  # 危化品
    has_hazardous_waste: bool = False  # 危废品
    involves_dust_explosion: bool = False  # 粉尘爆炸
    is_new_energy_welding: bool = False  # 新能源电焊
    has_large_storage_tank: bool = False  # 大型储罐
    # ── 小场所明细 ──
    venue_status: str = ""
    staff_count: str = ""
    venue_area: str = ""
    capacity: str = ""
    is_multistory: bool | None = None
    has_business_license: bool | None = None
    venue_category_major: str = ""
    venue_category_minor: str = ""
    is_multi_license: bool | None = None
    regulatory_dept: str = ""
    territorial_dept: str = ""
    venue_safety_inspector: str = ""
    venue_fire_instructor: str = ""
    venue_risk_level: str = ""
    involves_evacuation: bool | None = None
    involves_fire_safety: bool | None = None
    involves_electrical: bool | None = None
    involves_lpg: bool | None = None
    involves_pipe_gas: bool | None = None
    involves_other_fuel: bool | None = None
    involves_decoration_work: bool | None = None
    involves_three_in_one: bool | None = None
    involves_flammable_storage: bool | None = None
    extra: dict = {}  # 长尾自定义键值对（jsonb）


class EnterpriseUpdateRequest(BaseModel):
    """更新企业请求体（所有字段可选；仅显式传入的字段会被更新）。"""
    name: str | None = None
    code: str | None = None
    # ── 基础信息 ──
    industry: str | None = None
    region: str | None = None
    address: str | None = None
    legal_person: str | None = None
    person_in_charge: str | None = None
    safety_officer: str | None = None
    legal_person_phone: str | None = None
    person_in_charge_phone: str | None = None
    safety_officer_phone: str | None = None
    enterprise_category: list[str] | None = None
    asset_attribute: str | None = None
    # ── 规模信息 ──
    enterprise_scale: str | None = None
    employee_count: int | None = None
    annual_output_value: int | None = None
    # ── 安全信息 ──
    fire_level: str | None = None
    confined_space: list[str] | None = None
    confined_space_count: str | None = None
    confined_space_type: str | None = None
    confined_space_workers: str | None = None
    confined_space_hazards: str | None = None
    confined_space_op_subject: str | None = None
    confined_space_op_cycle: str | None = None
    confined_space_fixed_workers: str | None = None
    hazchem_names: str | None = None
    hazchem_op_subject: str | None = None
    hazchem_major_hazard: str | None = None
    hazchem_state: str | None = None
    hazchem_op_cycle: str | None = None
    hazchem_usage: str | None = None
    hazchem_annual_usage: str | None = None
    hazchem_storage: str | None = None
    hazchem_reserve: str | None = None
    hazchem_fixed_workers: str | None = None
    has_natural_gas: bool | None = None
    is_injection_molding: bool | None = None
    has_dormitory: bool | None = None
    has_hazardous_chemicals: bool | None = None
    has_hazardous_waste: bool | None = None
    involves_dust_explosion: bool | None = None
    is_new_energy_welding: bool | None = None
    has_large_storage_tank: bool | None = None
    # ── 小场所明细 ──
    venue_status: str | None = None
    staff_count: str | None = None
    venue_area: str | None = None
    capacity: str | None = None
    is_multistory: bool | None = None
    has_business_license: bool | None = None
    venue_category_major: str | None = None
    venue_category_minor: str | None = None
    is_multi_license: bool | None = None
    regulatory_dept: str | None = None
    territorial_dept: str | None = None
    venue_safety_inspector: str | None = None
    venue_fire_instructor: str | None = None
    venue_risk_level: str | None = None
    involves_evacuation: bool | None = None
    involves_fire_safety: bool | None = None
    involves_electrical: bool | None = None
    involves_lpg: bool | None = None
    involves_pipe_gas: bool | None = None
    involves_other_fuel: bool | None = None
    involves_decoration_work: bool | None = None
    involves_three_in_one: bool | None = None
    involves_flammable_storage: bool | None = None
    extra: dict | None = None


@router.get("")
async def list_enterprises_endpoint(
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    industry: Optional[str] = None,
    region: Optional[str] = None,
    enterprise_category: Optional[list[str]] = Query(default=None),
    enterprise_scale: Optional[str] = None,
    fire_level: Optional[str] = None,
    confined_space: Optional[list[str]] = Query(default=None),
    confined_space_count: Optional[str] = None,
    confined_space_type: Optional[str] = None,
    confined_space_workers: Optional[str] = None,
    confined_space_hazards: Optional[str] = None,
    confined_space_op_subject: Optional[str] = None,
    confined_space_op_cycle: Optional[str] = None,
    confined_space_fixed_workers: Optional[str] = None,
    hazchem_names: Optional[str] = None,
    hazchem_op_subject: Optional[str] = None,
    hazchem_major_hazard: Optional[str] = None,
    hazchem_state: Optional[str] = None,
    hazchem_op_cycle: Optional[str] = None,
    hazchem_usage: Optional[str] = None,
    hazchem_annual_usage: Optional[str] = None,
    hazchem_storage: Optional[str] = None,
    hazchem_reserve: Optional[str] = None,
    hazchem_fixed_workers: Optional[str] = None,
    # ── 小场所明细（核心筛选字段）──
    venue_status: Optional[str] = None,
    venue_category_major: Optional[str] = None,
    venue_category_minor: Optional[str] = None,
    venue_risk_level: Optional[str] = None,
    has_natural_gas: Optional[bool] = None,
    is_injection_molding: Optional[bool] = None,
    has_dormitory: Optional[bool] = None,
    has_hazardous_chemicals: Optional[bool] = None,
    has_hazardous_waste: Optional[bool] = None,
    involves_dust_explosion: Optional[bool] = None,
    is_new_energy_welding: Optional[bool] = None,
    has_large_storage_tank: Optional[bool] = None,
):
    """企业列表（分页 + 关键词 + 多维度筛选）。"""
    from app.services.svc_Ledger.enterprise_service import list_enterprises

    items, total = await list_enterprises(
        page=page,
        page_size=page_size,
        keyword=keyword or None,
        industry=industry,
        region=region,
        enterprise_category=enterprise_category,
        enterprise_scale=enterprise_scale,
        fire_level=fire_level,
        confined_space=confined_space,
        confined_space_count=confined_space_count,
        confined_space_type=confined_space_type,
        confined_space_workers=confined_space_workers,
        confined_space_hazards=confined_space_hazards,
        confined_space_op_subject=confined_space_op_subject,
        confined_space_op_cycle=confined_space_op_cycle,
        confined_space_fixed_workers=confined_space_fixed_workers,
        hazchem_names=hazchem_names,
        hazchem_op_subject=hazchem_op_subject,
        hazchem_major_hazard=hazchem_major_hazard,
        hazchem_state=hazchem_state,
        hazchem_op_cycle=hazchem_op_cycle,
        hazchem_usage=hazchem_usage,
        hazchem_annual_usage=hazchem_annual_usage,
        hazchem_storage=hazchem_storage,
        hazchem_reserve=hazchem_reserve,
        hazchem_fixed_workers=hazchem_fixed_workers,
        venue_status=venue_status,
        venue_category_major=venue_category_major,
        venue_category_minor=venue_category_minor,
        venue_risk_level=venue_risk_level,
        has_natural_gas=has_natural_gas,
        is_injection_molding=is_injection_molding,
        has_dormitory=has_dormitory,
        has_hazardous_chemicals=has_hazardous_chemicals,
        has_hazardous_waste=has_hazardous_waste,
        involves_dust_explosion=involves_dust_explosion,
        is_new_energy_welding=is_new_energy_welding,
        has_large_storage_tank=has_large_storage_tank,
    )
    return {
        "success": True,
        "data": {
            "total": total,
            "items": [_enterprise_to_dict(e) for e in items],
        },
    }


@router.get("/meta/industries")
async def list_industries_endpoint():
    """返回已有行业标签去重列表（筛选用）。"""
    from app.services.svc_Ledger.enterprise_service import list_industries

    return {"success": True, "data": await list_industries()}


@router.get("/meta/regions")
async def list_regions_endpoint():
    """返回已有区域标签去重列表（筛选用）。"""
    from app.services.svc_Ledger.enterprise_service import list_regions

    return {"success": True, "data": await list_regions()}


@router.get("/meta/extra-options")
async def list_extra_options_endpoint():
    """返回自定义字段的历史可选值：{字段名: [值...]}（供多值/标签输入选择）。"""
    from app.services.svc_Ledger.enterprise_service import list_extra_options

    return {"success": True, "data": await list_extra_options()}


@router.get("/meta/filter-fields")
async def list_filter_fields_endpoint():
    """返回企业表筛选字段元数据（动态条件构建器渲染用）。

    字段元数据同时是 service 层 build_clause 的白名单来源，单一源头不漂移。
    """
    from app.services.svc_Ledger.enterprise_service import get_enterprise_filter_fields

    return {
        "success": True,
        "data": [f.model_dump() for f in get_enterprise_filter_fields()],
    }


@router.post("/query")
async def query_enterprises_endpoint(req: EnterpriseFilterRequest):
    """企业复合条件查询（动态筛选构建器用）。

    与 GET /enterprises 并存：GET 保留向后兼容（简单筛选），
    POST 支持嵌套 AND/OR 组 + 多选 IN + 虚拟字段 keyword。
    """
    from app.services.svc_Ledger.enterprise_service import (
        enterprise_filter_error_to_422,
        list_enterprises_with_filter,
    )
    from app.services.svc_Common.filter_service import FilterError, FilterGroup

    # 把原始 dict 解析为 FilterGroup（白名单 + 操作符深度校验在 build_clause）
    try:
        filter_root = FilterGroup.model_validate(req.filter)
    except Exception as e:
        return {"success": False, "message": f"筛选条件结构不合法: {e}"}

    try:
        items, total = await list_enterprises_with_filter(
            filter_root,
            page=req.page,
            page_size=req.page_size,
        )
    except FilterError as e:
        # 白名单违规 / 操作符不支持 / 深度超限 → 422
        return JSONResponse(
            status_code=422,
            content=enterprise_filter_error_to_422(e),
        )

    return {
        "success": True,
        "data": {
            "total": total,
            "items": [_enterprise_to_dict(e) for e in items],
        },
    }


@router.get("/{enterprise_id}/profile")
async def get_enterprise_profile_endpoint(enterprise_id: int):
    """企业档案聚合（介绍页）：企业自身字段 + 巡查记录 + 厂中厂关系 + 危化品明细 + 相关材料。

    一次性返回介绍页所需的全部数据，避免前端发多个请求。
    """
    from app.services.svc_Ledger.enterprise_service import get_enterprise
    from app.services.svc_Ledger.inspection_service import list_records as list_inspections
    from app.services.svc_Ledger.factory_in_factory_service import (
        list_by_enterprise as list_factory_relations,
    )
    from app.services.svc_Ledger.hazardous_chemical_service import (
        list_by_enterprise as list_hazardous_chemicals,
    )

    ent = await get_enterprise(enterprise_id)
    if not ent:
        return {"success": False, "data": None, "message": "企业不存在"}

    inspections, _ = await list_inspections(
        enterprise_id=enterprise_id, page=1, page_size=200
    )
    factory_relations = await list_factory_relations(enterprise_id)
    hazardous_chemicals = await list_hazardous_chemicals(enterprise_id)

    # ── 相关材料 ──
    # Material.enterprise_codes 为 JSON 字符串（如 '["91320991xxx","91320992xxx"]'），
    # 用 PG 的 @> 操作符精确匹配包含企业信用代码的材料；公共通用材料排除（enterprise_codes='{}'）。
    materials = []
    try:
        from app.db import SessionLocal
        from sqlalchemy import text as sa_text

        async with SessionLocal() as db:
            ent_code_json = json.dumps([ent.code], ensure_ascii=False) if ent.code else json.dumps([""])
            result = await db.execute(
                sa_text(
                    """
                    SELECT id, uid, filename, source_format, page_count, status, created_at
                    FROM material
                    WHERE enterprise_codes IS NOT NULL
                      AND enterprise_codes != '{}'
                      AND enterprise_codes::jsonb @> :code_json
                    ORDER BY created_at DESC
                    LIMIT 100
                    """
                ),
                {"code_json": ent_code_json},
            )
            for row in result.fetchall():
                materials.append({
                    "id": row[0],
                    "uid": row[1],
                    "filename": row[2],
                    "source_format": row[3],
                    "page_count": row[4],
                    "status": row[5],
                    "created_at": row[6].strftime("%Y-%m-%d %H:%M:%S") if row[6] else None,
                })
    except Exception as exc:
        # 材料聚合失败不影响主档案返回
        import logging
        logging.getLogger(__name__).warning("profile materials query failed: %s", exc)
        materials = []

    return {
        "success": True,
        "data": {
            "enterprise": _enterprise_to_dict(ent),
            "inspections": inspections,
            "factory_relations": factory_relations,
            "hazardous_chemicals": hazardous_chemicals,
            "materials": materials,
        },
    }


@router.get("/{enterprise_id}")
async def get_enterprise_endpoint(enterprise_id: int):
    """企业详情。"""
    from app.services.svc_Ledger.enterprise_service import get_enterprise

    ent = await get_enterprise(enterprise_id)
    if not ent:
        return {"success": False, "data": None, "message": "企业不存在"}
    return {"success": True, "data": _enterprise_to_dict(ent)}


@router.post("")
async def create_enterprise_endpoint(body: EnterpriseCreateRequest, request: Request):
    """新建企业：code 18位校验 + 唯一性。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.enterprise_service import create_enterprise, EnterpriseError

    try:
        ent = await create_enterprise(
            name=body.name,
            code=body.code,
            industry=body.industry,
            region=body.region,
            address=body.address,
            legal_person=body.legal_person,
            person_in_charge=body.person_in_charge,
            safety_officer=body.safety_officer,
            legal_person_phone=body.legal_person_phone,
            person_in_charge_phone=body.person_in_charge_phone,
            safety_officer_phone=body.safety_officer_phone,
            enterprise_category=body.enterprise_category,
            asset_attribute=body.asset_attribute,
            enterprise_scale=body.enterprise_scale,
            employee_count=body.employee_count,
            annual_output_value=body.annual_output_value,
            fire_level=body.fire_level,
            confined_space=body.confined_space,
            confined_space_count=body.confined_space_count,
            confined_space_type=body.confined_space_type,
            confined_space_workers=body.confined_space_workers,
            confined_space_hazards=body.confined_space_hazards,
            confined_space_op_subject=body.confined_space_op_subject,
            confined_space_op_cycle=body.confined_space_op_cycle,
            confined_space_fixed_workers=body.confined_space_fixed_workers,
            hazchem_names=body.hazchem_names,
            hazchem_op_subject=body.hazchem_op_subject,
            hazchem_major_hazard=body.hazchem_major_hazard,
            hazchem_state=body.hazchem_state,
            hazchem_op_cycle=body.hazchem_op_cycle,
            hazchem_usage=body.hazchem_usage,
            hazchem_annual_usage=body.hazchem_annual_usage,
            hazchem_storage=body.hazchem_storage,
            hazchem_reserve=body.hazchem_reserve,
            hazchem_fixed_workers=body.hazchem_fixed_workers,
            venue_status=body.venue_status,
            staff_count=body.staff_count,
            venue_area=body.venue_area,
            capacity=body.capacity,
            is_multistory=body.is_multistory,
            has_business_license=body.has_business_license,
            venue_category_major=body.venue_category_major,
            venue_category_minor=body.venue_category_minor,
            is_multi_license=body.is_multi_license,
            regulatory_dept=body.regulatory_dept,
            territorial_dept=body.territorial_dept,
            venue_safety_inspector=body.venue_safety_inspector,
            venue_fire_instructor=body.venue_fire_instructor,
            venue_risk_level=body.venue_risk_level,
            involves_evacuation=body.involves_evacuation,
            involves_fire_safety=body.involves_fire_safety,
            involves_electrical=body.involves_electrical,
            involves_lpg=body.involves_lpg,
            involves_pipe_gas=body.involves_pipe_gas,
            involves_other_fuel=body.involves_other_fuel,
            involves_decoration_work=body.involves_decoration_work,
            involves_three_in_one=body.involves_three_in_one,
            involves_flammable_storage=body.involves_flammable_storage,
            has_natural_gas=body.has_natural_gas,
            is_injection_molding=body.is_injection_molding,
            has_dormitory=body.has_dormitory,
            has_hazardous_chemicals=body.has_hazardous_chemicals,
            has_hazardous_waste=body.has_hazardous_waste,
            involves_dust_explosion=body.involves_dust_explosion,
            is_new_energy_welding=body.is_new_energy_welding,
            has_large_storage_tank=body.has_large_storage_tank,
            extra=body.extra,
        )
    except EnterpriseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_action_async(
        action="enterprise.create",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="enterprise",
        resource_id=str(ent.id),
        request_payload=body.model_dump_json(),
    )
    return {"success": True, "data": _enterprise_to_dict(ent)}


@router.put("/{enterprise_id}")
async def update_enterprise_endpoint(
    enterprise_id: int,
    body: EnterpriseUpdateRequest,
    request: Request,
):
    """更新企业字段。

    用 model_fields_set 只透传客户端显式提交的字段：
    全量表单提交空数字/空文本时传 None → 服务层置 NULL；未提交的列保持不变。
    """
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.enterprise_service import update_enterprise, EnterpriseError

    payload = body.model_dump(exclude_unset=True)
    try:
        ent = await update_enterprise(enterprise_id, **payload)
    except TypeError as e:
        # 防御未知字段（正常情况下 pydantic 已拦截）
        raise HTTPException(status_code=422, detail=f"不支持的字段: {e}")
    except EnterpriseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not ent:
        return {"success": False, "data": None, "message": "企业不存在"}

    log_action_async(
        action="enterprise.update",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="enterprise",
        resource_id=str(ent.id),
        request_payload=body.model_dump_json(),
    )
    return {"success": True, "data": _enterprise_to_dict(ent)}


@router.delete("/{enterprise_id}")
async def delete_enterprise_endpoint(enterprise_id: int, request: Request):
    """删除企业（带删除保护：有关联巡查单禁止删）。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.enterprise_service import delete_enterprise, EnterpriseError

    try:
        ok = await delete_enterprise(enterprise_id)
    except EnterpriseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not ok:
        return {"success": False, "message": "企业不存在"}

    log_action_async(
        action="enterprise.delete",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="enterprise",
        resource_id=str(enterprise_id),
    )
    return {"success": True, "message": "删除成功"}
