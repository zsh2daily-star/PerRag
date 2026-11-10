"""企业主数据 service —— 提供企业表的增删改查 + 信用代码校验 + 删除保护。

企业是被监管对象主数据，不登录。industry/region 是类型标签，
code（统一社会信用代码）是跨库稳定身份，材料数组引用它。

本模块基于 BaseRepository（app/db/crud.py）做存取，本文件只写业务规则。
"""

import json
import re
from datetime import datetime
from typing import Optional, Union

from sqlalchemy import cast, text
from sqlalchemy.types import ARRAY, Text
from sqlmodel import select, or_

from app.db.crud import BaseRepository
from app.db.database import get_session_factory
from app.db.models import Enterprise, InspectionRecord
from app.services.svc_Common.filter_service import (
    FieldSpec,
    FilterError,
    FilterGroup,
    FilterLeaf,
    list_with_filter,
)
from app.services.svc_Common.filter_helpers import list_with_filter_wrapper

# repo 单例：本文件所有函数共用
enterprises = BaseRepository(Enterprise)
inspections = BaseRepository(InspectionRecord)

# 统一社会信用代码：18 位，由登记管理部门代码(1) + 机构类别代码(1) +
# 登记管理机关行政区划码(6) + 主体识别码(9) + 校验码(1) 组成。
# 简化校验：18 位字母+数字，以 9 开头（企业）。
_CODE_PATTERN = re.compile(r"^[0-9A-Z]{18}$")


class EnterpriseError(ValueError):
    """企业操作不合规（code 格式/唯一性/删除保护）。"""


def _validate_code(code: str) -> None:
    """校验统一社会信用代码格式。"""
    if not code:
        raise EnterpriseError("统一社会信用代码不能为空")
    code = code.strip().upper()
    if not _CODE_PATTERN.match(code):
        raise EnterpriseError("统一社会信用代码需 18 位字母/数字")
    if not code.startswith("9"):
        # 9 开头表示企业，其余为机关/社团/事业单位等
        raise EnterpriseError("企业统一社会信用代码应以 9 开头")


# update 的哨兵：区分"调用方未提供该字段（不改）"与"显式传 None（置空）"
_UNSET = object()

# ── 固定枚举值域（中文存储，空=未填报）──
_ENTERPRISE_CATEGORIES = ("劳密", "厂中厂出租", "厂中厂承租", "小场所", "规上", "搬离", "其他")
_ENTERPRISE_SCALES = ("规上", "规下")
_FIRE_LEVELS = ("二级", "三级", "重点单位")
_ASSET_ATTRIBUTES = ("韩资", "台资", "其他")
_CONFINED_SPACES = ("地上", "地下")

# 可空文本/枚举列（清空 → 置 NULL）
_SCALAR_NULLABLE_FIELDS = (
    "industry", "region", "address", "legal_person",
    "person_in_charge", "safety_officer",
    "legal_person_phone", "person_in_charge_phone", "safety_officer_phone",
    "asset_attribute",
    "enterprise_scale", "fire_level",
    "confined_space_count", "confined_space_type",
    "confined_space_workers", "confined_space_hazards",
    "confined_space_op_subject", "confined_space_op_cycle",
    "confined_space_fixed_workers",
    "hazchem_names", "hazchem_op_subject",
    "hazchem_major_hazard", "hazchem_state", "hazchem_op_cycle",
    "hazchem_usage", "hazchem_annual_usage", "hazchem_storage",
    "hazchem_reserve", "hazchem_fixed_workers",
    # ── 小场所明细（文本/枚举可空列）──
    "venue_status", "staff_count", "venue_area", "capacity",
    "venue_category_major", "venue_category_minor",
    "regulatory_dept", "territorial_dept",
    "venue_safety_inspector", "venue_fire_instructor", "venue_risk_level",
)

# 可空数组列（清空 → 置 NULL；非空 → list 去重保序）
_ARRAY_NULLABLE_FIELDS = ("enterprise_category", "confined_space")

# 安全风险点布尔列（False 也要写入）
_BOOL_FIELDS = (
    "has_natural_gas", "is_injection_molding", "has_dormitory",
    "has_hazardous_chemicals", "has_hazardous_waste",
    "involves_dust_explosion", "is_new_energy_welding",
    "has_large_storage_tank",
)


def _validate_enum(label: str, allowed: tuple, val) -> None:
    """枚举列只允许空（未填报）或允许值之一。"""
    if val is None:
        return
    if isinstance(val, (list, tuple, set)):
        for v in val:
            if v not in allowed:
                raise EnterpriseError(f"{label}只能是：{'/'.join(allowed)}")
        return
    if val not in allowed:
        raise EnterpriseError(f"{label}只能是：{'/'.join(allowed)}")


def _normalize_tags(val) -> Optional[list]:
    """多标签数组列归一（enterprise_category / confined_space）：去重保序、空值归 None。"""
    if val is None or val == "" or val == []:
        return None
    if isinstance(val, str):
        val = [val]
    seen: list = []
    seen_set: set = set()
    for v in val:
        if not v or not isinstance(v, str):
            continue
        v = v.strip()
        if v and v not in seen_set:
            seen.append(v)
            seen_set.add(v)
    return seen or None


def _validate_non_negative_int(label: str, val) -> None:
    """人数/产值类字段：允许 None（未填），否则必须非负整数（排除 bool）。"""
    if val is None:
        return
    if isinstance(val, bool) or not isinstance(val, int) or val < 0:
        raise EnterpriseError(f"{label}必须是非负整数")


def _normalize_extra(extra) -> Optional[dict]:
    """extra 统一归一为 dict（落 jsonb 列）。

    接受：dict / 合法 JSON 字符串；None 与空串归一为 None（列存 NULL）。
    空 dict {} 保留（语义=清空为无扩展字段，与 NULL 同效但由调用方决定）。

    值类型（一个 key 可对应单值或多值）：
    - 单值：字符串；
    - 多值：字符串数组（如下拉多选），数组元素必须是字符串，
      空串元素自动剔除，空数组保留。
    其余类型（数字/布尔/对象/嵌套数组）一律拒绝，保证数据规范、可聚合。
    """
    if extra is None or extra == "":
        return None
    if isinstance(extra, str):
        try:
            parsed = json.loads(extra)
        except json.JSONDecodeError:
            raise EnterpriseError("extra 不是合法 JSON")
        if not isinstance(parsed, dict):
            raise EnterpriseError("extra 必须是 JSON 对象")
        extra = parsed
    if not isinstance(extra, dict):
        raise EnterpriseError("extra 必须是 JSON 对象")

    normalized: dict = {}
    for key, val in extra.items():
        if not isinstance(key, str) or not key.strip():
            raise EnterpriseError("extra 的字段名必须是非空字符串")
        if isinstance(val, str):
            normalized[key] = val
        elif isinstance(val, list):
            items = []
            for item in val:
                if not isinstance(item, str):
                    raise EnterpriseError(
                        f"extra 字段「{key}」的多值必须都是字符串"
                    )
                if item != "":
                    items.append(item)
            normalized[key] = items
        elif val is None:
            # 显式 null 归一为空字符串（未填），而非存 JSON null
            normalized[key] = ""
        else:
            raise EnterpriseError(
                f"extra 字段「{key}」的值只能是字符串或字符串数组"
            )
    return normalized


async def create_enterprise(
    *,
    name: str,
    code: str,
    industry: Optional[str] = None,
    region: Optional[str] = None,
    address: Optional[str] = None,
    legal_person: Optional[str] = None,
    person_in_charge: Optional[str] = None,
    safety_officer: Optional[str] = None,
    enterprise_category: Optional[list] = None,
    asset_attribute: Optional[str] = None,
    enterprise_scale: Optional[str] = None,
    employee_count: Optional[int] = None,
    annual_output_value: Optional[int] = None,
    fire_level: Optional[str] = None,
    confined_space: Optional[list] = None,
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
    # ── 小场所明细 ──
    venue_status: Optional[str] = None,
    staff_count: Optional[str] = None,
    venue_area: Optional[str] = None,
    capacity: Optional[str] = None,
    is_multistory: Optional[bool] = None,
    has_business_license: Optional[bool] = None,
    venue_category_major: Optional[str] = None,
    venue_category_minor: Optional[str] = None,
    is_multi_license: Optional[bool] = None,
    regulatory_dept: Optional[str] = None,
    territorial_dept: Optional[str] = None,
    venue_safety_inspector: Optional[str] = None,
    venue_fire_instructor: Optional[str] = None,
    venue_risk_level: Optional[str] = None,
    involves_evacuation: Optional[bool] = None,
    involves_fire_safety: Optional[bool] = None,
    involves_electrical: Optional[bool] = None,
    involves_lpg: Optional[bool] = None,
    involves_pipe_gas: Optional[bool] = None,
    involves_other_fuel: Optional[bool] = None,
    involves_decoration_work: Optional[bool] = None,
    involves_three_in_one: Optional[bool] = None,
    involves_flammable_storage: Optional[bool] = None,
    has_natural_gas: bool = False,
    is_injection_molding: bool = False,
    has_dormitory: bool = False,
    has_hazardous_chemicals: bool = False,
    has_hazardous_waste: bool = False,
    involves_dust_explosion: bool = False,
    is_new_energy_welding: bool = False,
    has_large_storage_tank: bool = False,
    extra: Optional[dict] = None,
) -> Enterprise:
    """新建企业。code 唯一性 + 格式校验 + 枚举值校验。"""
    if not name or not name.strip():
        raise EnterpriseError("企业名称不能为空")
    code = code.strip().upper()
    _validate_code(code)
    enterprise_category = _normalize_tags(enterprise_category)
    confined_space = _normalize_tags(confined_space)
    _validate_enum("企业类型", _ENTERPRISE_CATEGORIES, enterprise_category)
    _validate_enum("资产属性", _ASSET_ATTRIBUTES, asset_attribute or None)
    _validate_enum("企业规模", _ENTERPRISE_SCALES, enterprise_scale or None)
    _validate_enum("消防重点单位", _FIRE_LEVELS, fire_level or None)
    _validate_enum("有限空间", _CONFINED_SPACES, confined_space)
    _validate_non_negative_int("职工人数", employee_count)
    _validate_non_negative_int("年产值", annual_output_value)

    if await enterprises.exists(code=code):
        raise EnterpriseError(f"统一社会信用代码 {code} 已存在")

    return await enterprises.create(
        name=name.strip(),
        code=code,
        industry=industry or None,
        region=region or None,
        address=address or None,
        legal_person=legal_person or None,
        person_in_charge=person_in_charge or None,
        safety_officer=safety_officer or None,
        enterprise_category=enterprise_category,
        asset_attribute=asset_attribute or None,
        enterprise_scale=enterprise_scale or None,
        employee_count=employee_count,
        annual_output_value=annual_output_value,
        fire_level=fire_level or None,
        confined_space=confined_space or None,
        confined_space_count=confined_space_count or None,
        confined_space_type=confined_space_type or None,
        confined_space_workers=confined_space_workers or None,
        confined_space_hazards=confined_space_hazards or None,
        hazchem_names=hazchem_names or None,
        hazchem_op_subject=hazchem_op_subject or None,
        hazchem_major_hazard=hazchem_major_hazard or None,
        hazchem_state=hazchem_state or None,
        hazchem_op_cycle=hazchem_op_cycle or None,
        hazchem_usage=hazchem_usage or None,
        hazchem_annual_usage=hazchem_annual_usage or None,
        hazchem_storage=hazchem_storage or None,
        hazchem_reserve=hazchem_reserve or None,
        hazchem_fixed_workers=hazchem_fixed_workers or None,
        # ── 小场所明细 ──
        venue_status=venue_status or None,
        staff_count=staff_count or None,
        venue_area=venue_area or None,
        capacity=capacity or None,
        is_multistory=is_multistory,
        has_business_license=has_business_license,
        venue_category_major=venue_category_major or None,
        venue_category_minor=venue_category_minor or None,
        is_multi_license=is_multi_license,
        regulatory_dept=regulatory_dept or None,
        territorial_dept=territorial_dept or None,
        venue_safety_inspector=venue_safety_inspector or None,
        venue_fire_instructor=venue_fire_instructor or None,
        venue_risk_level=venue_risk_level or None,
        involves_evacuation=involves_evacuation,
        involves_fire_safety=involves_fire_safety,
        involves_electrical=involves_electrical,
        involves_lpg=involves_lpg,
        involves_pipe_gas=involves_pipe_gas,
        involves_other_fuel=involves_other_fuel,
        involves_decoration_work=involves_decoration_work,
        involves_three_in_one=involves_three_in_one,
        involves_flammable_storage=involves_flammable_storage,
        has_natural_gas=bool(has_natural_gas),
        is_injection_molding=bool(is_injection_molding),
        has_dormitory=bool(has_dormitory),
        has_hazardous_chemicals=bool(has_hazardous_chemicals),
        has_hazardous_waste=bool(has_hazardous_waste),
        involves_dust_explosion=bool(involves_dust_explosion),
        is_new_energy_welding=bool(is_new_energy_welding),
        has_large_storage_tank=bool(has_large_storage_tank),
        extra=_normalize_extra(extra),
    )


async def list_enterprises(
    *,
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    industry: Optional[str] = None,
    region: Optional[str] = None,
    enterprise_category: Optional[Union[str, list]] = None,
    enterprise_scale: Optional[str] = None,
    fire_level: Optional[str] = None,
    confined_space: Optional[Union[str, list]] = None,
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
    # ── 小场所明细（核心筛选字段，其他字段通过 POST /enterprises/query 复合筛选）──
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
) -> tuple[list[Enterprise], int]:
    """分页列表 + 关键词(名称/code) + 多维度筛选。返回 (items, total)。

    所有筛选参数均为可选；bool 参数传 True 筛选有此标记的企业，
    传 None/不传表示不筛该字段。
    """
    stmt = select(Enterprise)
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(or_(
            Enterprise.name.ilike(kw),
            Enterprise.code.ilike(kw),
        ))
    if industry:
        stmt = stmt.where(Enterprise.industry == industry)
    if region:
        stmt = stmt.where(Enterprise.region == region)
    if enterprise_category:
        # 多标签数组列：传入 list 用 contains (@>)，传入 str 包成单元素 list
        cats = enterprise_category if isinstance(enterprise_category, list) else [enterprise_category]
        cats = [c for c in cats if c]
        if cats:
            stmt = stmt.where(Enterprise.enterprise_category.op('@>')(cast(cats, ARRAY(Text))))
    if enterprise_scale:
        stmt = stmt.where(Enterprise.enterprise_scale == enterprise_scale)
    if fire_level:
        stmt = stmt.where(Enterprise.fire_level == fire_level)
    if confined_space:
        # 多标签数组列：传入 list 用 contains (@>)，传入 str 包成单元素 list
        spaces = confined_space if isinstance(confined_space, list) else [confined_space]
        spaces = [c for c in spaces if c]
        if spaces:
            stmt = stmt.where(Enterprise.confined_space.op('@>')(cast(spaces, ARRAY(Text))))
    if confined_space_count:
        stmt = stmt.where(Enterprise.confined_space_count == confined_space_count)
    if confined_space_type:
        stmt = stmt.where(Enterprise.confined_space_type == confined_space_type)
    if confined_space_workers:
        stmt = stmt.where(Enterprise.confined_space_workers == confined_space_workers)
    if confined_space_hazards:
        stmt = stmt.where(Enterprise.confined_space_hazards == confined_space_hazards)
    if confined_space_op_subject:
        stmt = stmt.where(Enterprise.confined_space_op_subject == confined_space_op_subject)
    if confined_space_op_cycle:
        stmt = stmt.where(Enterprise.confined_space_op_cycle == confined_space_op_cycle)
    if confined_space_fixed_workers:
        stmt = stmt.where(Enterprise.confined_space_fixed_workers == confined_space_fixed_workers)
    if hazchem_names:
        stmt = stmt.where(Enterprise.hazchem_names == hazchem_names)
    if hazchem_op_subject:
        stmt = stmt.where(Enterprise.hazchem_op_subject == hazchem_op_subject)
    if hazchem_major_hazard:
        stmt = stmt.where(Enterprise.hazchem_major_hazard == hazchem_major_hazard)
    if hazchem_state:
        stmt = stmt.where(Enterprise.hazchem_state == hazchem_state)
    if hazchem_op_cycle:
        stmt = stmt.where(Enterprise.hazchem_op_cycle == hazchem_op_cycle)
    if hazchem_usage:
        stmt = stmt.where(Enterprise.hazchem_usage == hazchem_usage)
    if hazchem_annual_usage:
        stmt = stmt.where(Enterprise.hazchem_annual_usage == hazchem_annual_usage)
    if hazchem_storage:
        stmt = stmt.where(Enterprise.hazchem_storage == hazchem_storage)
    if hazchem_reserve:
        stmt = stmt.where(Enterprise.hazchem_reserve == hazchem_reserve)
    if hazchem_fixed_workers:
        stmt = stmt.where(Enterprise.hazchem_fixed_workers == hazchem_fixed_workers)
    # ── 小场所明细筛选（核心字段）──
    if venue_status:
        stmt = stmt.where(Enterprise.venue_status == venue_status)
    if venue_category_major:
        stmt = stmt.where(Enterprise.venue_category_major == venue_category_major)
    if venue_category_minor:
        stmt = stmt.where(Enterprise.venue_category_minor == venue_category_minor)
    if venue_risk_level:
        stmt = stmt.where(Enterprise.venue_risk_level == venue_risk_level)
    # bool 筛选：仅 True 时过滤（False = 不限）
    for field, val in (
        (Enterprise.has_natural_gas, has_natural_gas),
        (Enterprise.is_injection_molding, is_injection_molding),
        (Enterprise.has_dormitory, has_dormitory),
        (Enterprise.has_hazardous_chemicals, has_hazardous_chemicals),
        (Enterprise.has_hazardous_waste, has_hazardous_waste),
        (Enterprise.involves_dust_explosion, involves_dust_explosion),
        (Enterprise.is_new_energy_welding, is_new_energy_welding),
        (Enterprise.has_large_storage_tank, has_large_storage_tank),
    ):
        if val:
            stmt = stmt.where(field.is_(True))

    return await enterprises.list(
        stmt=stmt,
        page=page,
        page_size=page_size,
        order_by=[Enterprise.id.desc()],
    )


async def list_extra_options() -> dict:
    """聚合全部企业 extra 中每个字段已出现过的值（多选下拉的历史选项）。

    返回 {字段名: [去重值...]}：单值字段取其字符串本身，多值字段把
    jsonb 数组展开，空值剔除。供前端自定义字段做多选/标签输入的建议选项。
    """
    sql = text(
        """
        SELECT j.key AS field_key, ARRAY_AGG(DISTINCT lat.v) AS vals
        FROM enterprise e
        CROSS JOIN LATERAL jsonb_each(e.extra) AS j(key, value)
        CROSS JOIN LATERAL (
            SELECT elem AS v
            FROM jsonb_array_elements_text(j.value) AS elem
            WHERE jsonb_typeof(j.value) = 'array'
            UNION ALL
            SELECT j.value #>> '{}'
            WHERE jsonb_typeof(j.value) = 'string'
        ) AS lat
        WHERE e.extra IS NOT NULL
          AND jsonb_typeof(e.extra) = 'object'
          AND lat.v <> ''
        GROUP BY j.key
        ORDER BY j.key
        """
    )
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(sql)).all()
    return {row.field_key: sorted(row.vals or []) for row in rows}


async def get_enterprise(enterprise_id: int) -> Optional[Enterprise]:
    """按 id 查单条。"""
    return await enterprises.get(enterprise_id)


async def get_enterprise_by_code(code: str) -> Optional[Enterprise]:
    """按信用代码查单条。"""
    return await enterprises.get_by(code=code.upper())


async def get_enterprises_by_codes(codes: list[str]) -> list[Enterprise]:
    """批量按信用代码查企业（材料上传时校验 enterprise_codes 存在性）。"""
    if not codes:
        return []
    stmt = select(Enterprise).where(
        Enterprise.code.in_([c.upper() for c in codes])
    )
    items, _ = await enterprises.list(stmt=stmt, page=1, page_size=500)
    return items


async def update_enterprise(
    enterprise_id: int,
    *,
    name=_UNSET,
    code=_UNSET,
    industry=_UNSET,
    region=_UNSET,
    address=_UNSET,
    legal_person=_UNSET,
    legal_person_phone=_UNSET,
    person_in_charge=_UNSET,
    person_in_charge_phone=_UNSET,
    safety_officer=_UNSET,
    safety_officer_phone=_UNSET,
    enterprise_category: Optional[list] = _UNSET,
    asset_attribute=_UNSET,
    enterprise_scale=_UNSET,
    employee_count=_UNSET,
    annual_output_value=_UNSET,
    fire_level=_UNSET,
    confined_space: Optional[list] = _UNSET,
    confined_space_count=_UNSET,
    confined_space_type=_UNSET,
    confined_space_workers=_UNSET,
    confined_space_hazards=_UNSET,
    confined_space_op_subject=_UNSET,
    confined_space_op_cycle=_UNSET,
    confined_space_fixed_workers=_UNSET,
    hazchem_names=_UNSET,
    hazchem_op_subject=_UNSET,
    hazchem_major_hazard=_UNSET,
    hazchem_state=_UNSET,
    hazchem_op_cycle=_UNSET,
    hazchem_usage=_UNSET,
    hazchem_annual_usage=_UNSET,
    hazchem_storage=_UNSET,
    hazchem_reserve=_UNSET,
    hazchem_fixed_workers=_UNSET,
    # ── 小场所明细 ──
    venue_status=_UNSET,
    staff_count=_UNSET,
    venue_area=_UNSET,
    capacity=_UNSET,
    is_multistory=_UNSET,
    has_business_license=_UNSET,
    venue_category_major=_UNSET,
    venue_category_minor=_UNSET,
    is_multi_license=_UNSET,
    regulatory_dept=_UNSET,
    territorial_dept=_UNSET,
    venue_safety_inspector=_UNSET,
    venue_fire_instructor=_UNSET,
    venue_risk_level=_UNSET,
    involves_evacuation=_UNSET,
    involves_fire_safety=_UNSET,
    involves_electrical=_UNSET,
    involves_lpg=_UNSET,
    involves_pipe_gas=_UNSET,
    involves_other_fuel=_UNSET,
    involves_decoration_work=_UNSET,
    involves_three_in_one=_UNSET,
    involves_flammable_storage=_UNSET,
    has_natural_gas=_UNSET,
    is_injection_molding=_UNSET,
    has_dormitory=_UNSET,
    has_hazardous_chemicals=_UNSET,
    has_hazardous_waste=_UNSET,
    involves_dust_explosion=_UNSET,
    is_new_energy_welding=_UNSET,
    has_large_storage_tank=_UNSET,
    extra=_UNSET,
) -> Optional[Enterprise]:
    """按 id 更新企业字段（局部更新语义）。

    - 未提供的参数（哨兵 _UNSET）不改；
    - 可空标量显式传 None → 置 NULL（全量表单清空输入框）；
    - 布尔显式传 True/False 即写入；
    - code 改动时重新校验唯一性。
    """
    if name is not _UNSET:
        if not name or not name.strip():
            raise EnterpriseError("企业名称不能为空")
        name = name.strip()
    # code 为非空身份字段：显式传 None/空串（全量表单提交、企业原本无代码）按"不改"处理
    if code is not _UNSET:
        if code is None or not str(code).strip():
            code = _UNSET
        else:
            code = str(code).strip().upper()
            _validate_code(code)
            existing = await enterprises.get_by(code=code)
            if existing and existing.id != enterprise_id:
                raise EnterpriseError(f"统一社会信用代码 {code} 已被其他企业占用")

    values: dict = {}
    set_null: set[str] = set()

    # 文本/枚举可空列（name/code 不置空，仅写入；enterprise_category 为数组列单独处理）
    for field, val in (
        ("name", name), ("code", code),
        ("industry", industry), ("region", region),
        ("address", address), ("legal_person", legal_person),
        ("legal_person_phone", legal_person_phone),
        ("person_in_charge", person_in_charge),
        ("person_in_charge_phone", person_in_charge_phone),
        ("safety_officer", safety_officer),
        ("safety_officer_phone", safety_officer_phone),
        ("asset_attribute", asset_attribute),
        ("enterprise_scale", enterprise_scale),
        ("fire_level", fire_level),
        ("confined_space_count", confined_space_count),
        ("confined_space_type", confined_space_type),
        ("confined_space_workers", confined_space_workers),
        ("confined_space_hazards", confined_space_hazards),
        ("confined_space_op_subject", confined_space_op_subject),
        ("confined_space_op_cycle", confined_space_op_cycle),
        ("confined_space_fixed_workers", confined_space_fixed_workers),
        ("hazchem_names", hazchem_names),
        ("hazchem_op_subject", hazchem_op_subject),
        ("hazchem_major_hazard", hazchem_major_hazard),
        ("hazchem_state", hazchem_state),
        ("hazchem_op_cycle", hazchem_op_cycle),
        ("hazchem_usage", hazchem_usage),
        ("hazchem_annual_usage", hazchem_annual_usage),
        ("hazchem_storage", hazchem_storage),
        ("hazchem_reserve", hazchem_reserve),
        ("hazchem_fixed_workers", hazchem_fixed_workers),
        # ── 小场所明细（文本/枚举类，可空）──
        ("venue_status", venue_status),
        ("staff_count", staff_count),
        ("venue_area", venue_area),
        ("capacity", capacity),
        ("venue_category_major", venue_category_major),
        ("venue_category_minor", venue_category_minor),
        ("regulatory_dept", regulatory_dept),
        ("territorial_dept", territorial_dept),
        ("venue_safety_inspector", venue_safety_inspector),
        ("venue_fire_instructor", venue_fire_instructor),
        ("venue_risk_level", venue_risk_level),
    ):
        if val is _UNSET:
            continue
        # 可空列收到空串（前端清空下拉/输入框）归一为 NULL
        if (
            field in _SCALAR_NULLABLE_FIELDS
            and isinstance(val, str)
            and val.strip() == ""
        ):
            val = None
        values[field] = val
        if val is None and field in _SCALAR_NULLABLE_FIELDS:
            set_null.add(field)

    # 数组可空列：enterprise_category / confined_space（多标签 list）
    if enterprise_category is not _UNSET:
        normalized = _normalize_tags(enterprise_category)
        _validate_enum("企业类型", _ENTERPRISE_CATEGORIES, normalized)
        if normalized is None:
            set_null.add("enterprise_category")
        else:
            values["enterprise_category"] = normalized

    if confined_space is not _UNSET:
        normalized_cs = _normalize_tags(confined_space)
        _validate_enum("有限空间", _CONFINED_SPACES, normalized_cs)
        if normalized_cs is None:
            set_null.add("confined_space")
        else:
            values["confined_space"] = normalized_cs

    # 枚举值校验：未提供（哨兵）跳过；空串按未填报处理
    _validate_enum("资产属性", _ASSET_ATTRIBUTES,
                   None if asset_attribute is _UNSET
                   else (asset_attribute or None))
    _validate_enum("企业规模", _ENTERPRISE_SCALES,
                   None if enterprise_scale is _UNSET
                   else (enterprise_scale or None))
    _validate_enum("消防重点单位", _FIRE_LEVELS,
                   None if fire_level is _UNSET
                   else (fire_level or None))

    # 数字可空列：清空输入框 → None → 置 NULL；非空必须是非负整数
    for field, label, val in (
        ("employee_count", "职工人数", employee_count),
        ("annual_output_value", "年产值", annual_output_value),
    ):
        if val is _UNSET:
            continue
        _validate_non_negative_int(label, val)
        values[field] = val
        if val is None:
            set_null.add(field)

    # 布尔列：False 是有效值必须写入（repo 仅跳过 None）
    bool_inputs = (
        ("has_natural_gas", has_natural_gas),
        ("is_injection_molding", is_injection_molding),
        ("has_dormitory", has_dormitory),
        ("has_hazardous_chemicals", has_hazardous_chemicals),
        ("has_hazardous_waste", has_hazardous_waste),
        ("involves_dust_explosion", involves_dust_explosion),
        ("is_new_energy_welding", is_new_energy_welding),
        ("has_large_storage_tank", has_large_storage_tank),
    )
    for field, val in bool_inputs:
        if val is not _UNSET:
            values[field] = bool(val)

    # 小场所布尔三态列：None/True/False 都是有效值（三态），全量写入
    venue_bool_inputs = (
        ("is_multistory", is_multistory),
        ("has_business_license", has_business_license),
        ("is_multi_license", is_multi_license),
        ("involves_evacuation", involves_evacuation),
        ("involves_fire_safety", involves_fire_safety),
        ("involves_electrical", involves_electrical),
        ("involves_lpg", involves_lpg),
        ("involves_pipe_gas", involves_pipe_gas),
        ("involves_other_fuel", involves_other_fuel),
        ("involves_decoration_work", involves_decoration_work),
        ("involves_three_in_one", involves_three_in_one),
        ("involves_flammable_storage", involves_flammable_storage),
    )
    for field, val in venue_bool_inputs:
        if val is not _UNSET:
            # 三态：None → 置 NULL；True/False → 写入
            if val is None:
                set_null.add(field)
            else:
                values[field] = bool(val)

    # extra jsonb：显式 None 置 NULL；{} 等 dict 直接写入
    if extra is not _UNSET:
        normalized_extra = _normalize_extra(extra)
        if normalized_extra is None:
            set_null.add("extra")
        else:
            values["extra"] = normalized_extra

    if not values and not set_null:
        return await enterprises.get(enterprise_id)

    values["updated_at"] = datetime.now()
    return await enterprises.update(
        enterprise_id,
        set_null=set_null,
        **values,
    )


async def delete_enterprise(enterprise_id: int) -> bool:
    """删除企业（带删除保护）。

    保护规则：有关联巡查单（inspection_record.enterprise_id）时禁止删。
    材料用数组弱引用，无法做外键约束，此处不拦截材料（脏数据由后续清理）。
    """
    # 删除保护：同一事务内查引用 + 删除
    async with BaseRepository.session_scope() as s:
        ent = await enterprises.get(enterprise_id, session=s)
        if not ent:
            return False
        insp_count = await inspections.count(session=s, enterprise_id=enterprise_id)
        if insp_count > 0:
            raise EnterpriseError(
                f"该企业有 {insp_count} 条巡查记录，禁止删除（请先处理关联巡查单）"
            )
        return await enterprises.delete(enterprise_id, session=s, commit=False)


async def list_industries() -> list[str]:
    """返回已有 industry 的去重列表（前端筛选用）。"""
    stmt = select(Enterprise.industry).distinct().where(
        Enterprise.industry.is_not(None)
    )
    from app.db.database import get_session_factory
    async with get_session_factory()() as s:
        rows = (await s.execute(stmt)).all()
        return [r[0] for r in rows if r[0]]


async def list_regions() -> list[str]:
    """返回已有 region 的去重列表（前端筛选用）。"""
    stmt = select(Enterprise.region).distinct().where(
        Enterprise.region.is_not(None)
    )
    from app.db.database import get_session_factory
    async with get_session_factory()() as s:
        rows = (await s.execute(stmt)).all()
        return [r[0] for r in rows if r[0]]


# ── 复合筛选（动态条件构建器）─────────────────────────────────
# FieldSpec 同时供 GET /enterprises/meta/filter-fields（前端 UI 渲染）和
# build_clause 白名单校验使用，单一来源避免双份漂移。
# 字段允许的 operator 集合与 field.type 一一对应（filter_service 的
# _apply_operator 翻译）。
ENTERPRISE_FILTER_FIELDS: dict[str, FieldSpec] = {
    # ── 虚拟字段：keyword 走 name OR code 多字段模糊匹配 ──
    "keyword": FieldSpec(
        key="keyword",
        label="关键词",
        type="keyword",
        operators=["like", "like_not"],
        is_virtual=True,
    ),
    # ── 文本字段：eq / neq / like / like_not / is_null / is_not_null ──
    "name":           FieldSpec(key="name", label="企业名称", type="text",
                                operators=["eq", "neq", "like", "like_not",
                                           "is_null", "is_not_null"]),
    "code":           FieldSpec(key="code", label="信用代码", type="text",
                                operators=["eq", "neq", "like", "like_not",
                                           "is_null", "is_not_null"]),
    "address":        FieldSpec(key="address", label="详细地址", type="text",
                                operators=["eq", "neq", "like", "like_not",
                                           "is_null", "is_not_null"]),
    "legal_person":   FieldSpec(key="legal_person", label="法定代表人", type="text",
                                operators=["eq", "neq", "like", "like_not",
                                           "is_null", "is_not_null"]),
    "person_in_charge": FieldSpec(key="person_in_charge", label="企业负责人",
                                  type="text", operators=["eq", "neq", "like",
                                  "like_not", "is_null", "is_not_null"]),
    "safety_officer": FieldSpec(key="safety_officer", label="企业安全员", type="text",
                                operators=["eq", "neq", "like", "like_not",
                                           "is_null", "is_not_null"]),
    "legal_person_phone": FieldSpec(key="legal_person_phone", label="法人电话", type="text",
                                    operators=["eq", "neq", "like", "like_not",
                                               "is_null", "is_not_null"]),
    "person_in_charge_phone": FieldSpec(key="person_in_charge_phone", label="企业负责人电话",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "safety_officer_phone": FieldSpec(key="safety_officer_phone", label="企业安全员电话", type="text",
                                      operators=["eq", "neq", "like", "like_not",
                                                 "is_null", "is_not_null"]),
    # ── 枚举字段（动态选项）：industry / region 从 DB 拉 ──
    "industry":       FieldSpec(key="industry", label="行业", type="enum",
                                operators=["eq", "neq", "in", "not_in",
                                           "is_null", "is_not_null"],
                                options_source="/enterprises/meta/industries"),
    "region":         FieldSpec(key="region", label="所在区域", type="enum",
                                operators=["eq", "neq", "in", "not_in",
                                           "is_null", "is_not_null"],
                                options_source="/enterprises/meta/regions"),
    # ── 枚举字段（固定字典内联）──
    "enterprise_category": FieldSpec(key="enterprise_category", label="企业类型",
                                     type="enum_array",
                                     operators=["contains", "overlaps",
                                                "is_null", "is_not_null"],
                                     options=list(_ENTERPRISE_CATEGORIES)),
    "asset_attribute":     FieldSpec(key="asset_attribute", label="资产属性",
                                     type="enum",
                                     operators=["eq", "neq", "in", "not_in",
                                                "is_null", "is_not_null"],
                                     options=list(_ASSET_ATTRIBUTES)),
    "enterprise_scale":    FieldSpec(key="enterprise_scale", label="企业规模",
                                     type="enum",
                                     operators=["eq", "neq", "in", "not_in",
                                                "is_null", "is_not_null"],
                                     options=list(_ENTERPRISE_SCALES)),
    "fire_level":          FieldSpec(key="fire_level", label="消防重点单位等级",
                                     type="enum",
                                     operators=["eq", "neq", "in", "not_in",
                                                "is_null", "is_not_null"],
                                     options=list(_FIRE_LEVELS)),
    "confined_space":      FieldSpec(key="confined_space", label="有限空间位置",
                                     type="enum_array",
                                     operators=["contains", "overlaps",
                                                "is_null", "is_not_null"],
                                     options=list(_CONFINED_SPACES)),
    # ── 有限空间明细（文本字段）──
    "confined_space_count":   FieldSpec(key="confined_space_count", label="有限空间数量",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "confined_space_type":    FieldSpec(key="confined_space_type", label="有限空间类型",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "confined_space_workers":  FieldSpec(key="confined_space_workers", label="有限空间作业人数",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "confined_space_hazards": FieldSpec(key="confined_space_hazards", label="有限空间危险因素",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "confined_space_op_subject": FieldSpec(key="confined_space_op_subject", label="有限空间作业主体",
                                           type="text", operators=["eq", "neq", "like",
                                           "like_not", "is_null", "is_not_null"]),
    "confined_space_op_cycle": FieldSpec(key="confined_space_op_cycle", label="有限空间作业周期",
                                         type="text", operators=["eq", "neq", "like",
                                         "like_not", "is_null", "is_not_null"]),
    "confined_space_fixed_workers": FieldSpec(key="confined_space_fixed_workers", label="有限空间作业人员是否固定",
                                              type="text", operators=["eq", "neq", "like",
                                              "like_not", "is_null", "is_not_null"]),
    # ── 危化品明细（文本字段）──
    "hazchem_names":         FieldSpec(key="hazchem_names", label="危化品名称",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "hazchem_op_subject":    FieldSpec(key="hazchem_op_subject", label="作业主体",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "hazchem_major_hazard":  FieldSpec(key="hazchem_major_hazard", label="是否重大危险源",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "hazchem_state":         FieldSpec(key="hazchem_state", label="危化品状态",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "hazchem_op_cycle":      FieldSpec(key="hazchem_op_cycle", label="作业周期",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "hazchem_usage":         FieldSpec(key="hazchem_usage", label="危化品用途",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "hazchem_annual_usage":  FieldSpec(key="hazchem_annual_usage", label="年度使用量",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "hazchem_storage":       FieldSpec(key="hazchem_storage", label="存储方式",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "hazchem_reserve":       FieldSpec(key="hazchem_reserve", label="危化品储量",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "hazchem_fixed_workers": FieldSpec(key="hazchem_fixed_workers", label="作业人员是否固定",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    # ── 小场所明细（文本类）──
    "venue_status":          FieldSpec(key="venue_status", label="场所状态",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "staff_count":           FieldSpec(key="staff_count", label="从业人数",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "venue_area":            FieldSpec(key="venue_area", label="场所面积",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "capacity":              FieldSpec(key="capacity", label="承载人数",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "venue_category_major":  FieldSpec(key="venue_category_major", label="场所大类",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "venue_category_minor":  FieldSpec(key="venue_category_minor", label="场所小类",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "regulatory_dept":       FieldSpec(key="regulatory_dept", label="专业监管部门",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "territorial_dept":      FieldSpec(key="territorial_dept", label="属地管理部门",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    "venue_safety_inspector": FieldSpec(key="venue_safety_inspector", label="安全巡查员",
                                         type="text", operators=["eq", "neq", "like",
                                         "like_not", "is_null", "is_not_null"]),
    "venue_fire_instructor": FieldSpec(key="venue_fire_instructor", label="消防指导员",
                                         type="text", operators=["eq", "neq", "like",
                                         "like_not", "is_null", "is_not_null"]),
    "venue_risk_level":      FieldSpec(key="venue_risk_level", label="场所风险等级",
                                        type="text", operators=["eq", "neq", "like",
                                        "like_not", "is_null", "is_not_null"]),
    # ── 小场所明细（布尔三态：未填报/是/否）──
    "is_multistory":             FieldSpec(key="is_multistory", label="是否占用多层",
                                            type="bool", operators=["is_true", "is_false", "is_null", "is_not_null"]),
    "has_business_license":      FieldSpec(key="has_business_license", label="有无营业执照",
                                            type="bool", operators=["is_true", "is_false", "is_null", "is_not_null"]),
    "is_multi_license":          FieldSpec(key="is_multi_license", label="是否一店多证",
                                            type="bool", operators=["is_true", "is_false", "is_null", "is_not_null"]),
    "involves_evacuation":       FieldSpec(key="involves_evacuation", label="涉及疏散安全",
                                            type="bool", operators=["is_true", "is_false", "is_null", "is_not_null"]),
    "involves_fire_safety":      FieldSpec(key="involves_fire_safety", label="涉及消防安全",
                                            type="bool", operators=["is_true", "is_false", "is_null", "is_not_null"]),
    "involves_electrical":       FieldSpec(key="involves_electrical", label="涉及用电安全",
                                            type="bool", operators=["is_true", "is_false", "is_null", "is_not_null"]),
    "involves_lpg":              FieldSpec(key="involves_lpg", label="涉及瓶装液化气",
                                            type="bool", operators=["is_true", "is_false", "is_null", "is_not_null"]),
    "involves_pipe_gas":         FieldSpec(key="involves_pipe_gas", label="涉及管道天然气",
                                            type="bool", operators=["is_true", "is_false", "is_null", "is_not_null"]),
    "involves_other_fuel":       FieldSpec(key="involves_other_fuel", label="涉及其他燃料",
                                            type="bool", operators=["is_true", "is_false", "is_null", "is_not_null"]),
    "involves_decoration_work":  FieldSpec(key="involves_decoration_work", label="涉及装饰装修及危险作业",
                                            type="bool", operators=["is_true", "is_false", "is_null", "is_not_null"]),
    "involves_three_in_one":     FieldSpec(key="involves_three_in_one", label="涉及三合一场所",
                                            type="bool", operators=["is_true", "is_false", "is_null", "is_not_null"]),
    "involves_flammable_storage": FieldSpec(key="involves_flammable_storage", label="涉及储存易燃易爆物品",
                                              type="bool", operators=["is_true", "is_false", "is_null", "is_not_null"]),
    # ── 数字字段 ──
    "employee_count":      FieldSpec(key="employee_count", label="职工人数",
                                     type="number",
                                     operators=["eq", "neq", "gt", "lt", "gte", "lte",
                                                "is_null", "is_not_null"]),
    "annual_output_value": FieldSpec(key="annual_output_value", label="年产值(万元)",
                                     type="number",
                                     operators=["eq", "neq", "gt", "lt", "gte", "lte",
                                                "is_null", "is_not_null"]),
    # ── 布尔字段（8 个风险标记）：仅 is_true/is_false/is_null/is_not_null ──
    **{
        b: FieldSpec(
            key=b,
            label={
                "has_natural_gas": "天然气",
                "is_injection_molding": "注塑企业",
                "has_dormitory": "宿舍",
                "has_hazardous_chemicals": "危化品",
                "has_hazardous_waste": "危废品",
                "involves_dust_explosion": "粉尘爆炸",
                "is_new_energy_welding": "新能源电焊",
                "has_large_storage_tank": "大型储罐",
            }[b],
            type="bool",
            operators=["is_true", "is_false", "is_null", "is_not_null"],
        )
        for b in _BOOL_FIELDS
    },
    # ── 园区归属复核标记（非风险标签，独立于 _BOOL_FIELDS）──
    "out_of_park": FieldSpec(
        key="out_of_park", label="非园区待删", type="bool",
        operators=["is_true", "is_false", "is_null", "is_not_null"],
    ),
}


def get_enterprise_filter_fields() -> list[FieldSpec]:
    """返回企业表筛选字段元数据（GET /enterprises/meta/filter-fields 用）。

    顺序按：keyword → 文本 → 枚举(动态) → 枚举(固定) → 数字 → 布尔。
    """
    text_order = ["name", "code", "address", "legal_person",
                  "person_in_charge", "safety_officer",
                  "legal_person_phone", "person_in_charge_phone", "safety_officer_phone",
                  "confined_space_count", "confined_space_type",
                  "confined_space_workers", "confined_space_hazards",
                  "confined_space_op_subject", "confined_space_op_cycle",
                  "confined_space_fixed_workers",
                  "hazchem_names", "hazchem_op_subject",
                  "hazchem_major_hazard", "hazchem_state", "hazchem_op_cycle",
                  "hazchem_usage", "hazchem_annual_usage", "hazchem_storage",
                  "hazchem_reserve", "hazchem_fixed_workers",
                  # ── 小场所明细（文本类）──
                  "venue_status", "staff_count", "venue_area", "capacity",
                  "venue_category_major", "venue_category_minor",
                  "regulatory_dept", "territorial_dept",
                  "venue_safety_inspector", "venue_fire_instructor",
                  "venue_risk_level"]
    enum_dyn = ["industry", "region"]
    enum_fixed = ["enterprise_category", "asset_attribute",
                  "enterprise_scale", "fire_level", "confined_space"]
    num_order = ["employee_count", "annual_output_value"]
    # 主表 8 个风险标签（has_xxx/involves_xxx 二态 bool）
    bool_order = list(_BOOL_FIELDS)
    # 小场所布尔三态字段
    venue_bool_order = [
        "is_multistory", "has_business_license", "is_multi_license",
        "involves_evacuation", "involves_fire_safety", "involves_electrical",
        "involves_lpg", "involves_pipe_gas", "involves_other_fuel",
        "involves_decoration_work", "involves_three_in_one",
        "involves_flammable_storage",
    ]
    ordered_keys = (
        ["keyword"]
        + text_order + enum_dyn + enum_fixed + num_order + bool_order
        + venue_bool_order
        + ["out_of_park"]
    )
    return [ENTERPRISE_FILTER_FIELDS[k] for k in ordered_keys]


async def list_enterprises_with_filter(
    filter_root,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Enterprise], int]:
    """复合条件分页查询入口（POST /enterprises/query 用）。

    filter_root 可以是 FilterGroup（嵌套 AND/OR）、FilterLeaf（单条件）
    或 None（等同全表）。FilterError 由调用方（main.py endpoint）转 422。
    """
    return await list_with_filter(
        Enterprise,
        enterprises,
        filter_root,
        ENTERPRISE_FILTER_FIELDS,
        page=page,
        page_size=page_size,
        order_by=[Enterprise.id.desc()],
    )


def enterprise_filter_error_to_422(e: FilterError) -> dict:
    """把 FilterError 翻译为 endpoint 层 422 响应体（main.py 用）。"""
    return {"detail": str(e)}
