"""导入写入层（WRITER）—— 以 SQL 表字段特性为最小单元，自底向上逐层封装。

分层：
  L1  write_fields / create_record     最小写入单元：只管写入，不判断
  L2  FieldSpec + merge_field           判断层：三维字段规格 -> 合并值或 SKIP
  L3  TableWriter.preview / commit      编排层：preview=resolve+L2(不写) / commit=L1写
  L4  import_parser.aggregate_by_key    Excel 解析+按主键聚合 -> [(identity, fields), ...]
  TOOL mcp_tools_import.py             agent 入口：3 工具分层暴露给 agent

L3 拆分（用户 09-15 设计：L4->agent->L3->agent->L2）：
  preview(identity, fields) -> PreviewResult  resolve + compute_changes(L2)，不写库
  commit(identity, fields)  -> WriteResult    write_fields(L1) 落库
  agent 在 preview 和 commit 之间有控制权：审阅变更、处理错误、逐企业决策

L3 驱动方向：遍历 agent 给的 fields 字典，逐个查 field_specs -> merge_field

L2 三维分类：
  维度1  is_identity    主键 vs 其他    -> 主键更新时永不覆盖
  维度2  data_type      string|value|array|string_array -> 决定合并机制
  维度3  audit_weight   important|general -> 重要修改在日志/报告中突出

合并策略由维度 1+2 派生：
  主键        -> SKIP
  string      -> 追加（默认）/ 覆盖（override=True，上级决定）
  value       -> 后来值（新值非空就用，空保留旧值）
  array       -> key 级合并（只增不删）
  string_array -> list 级 union（去重保序，只增不删，旧值在前）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlmodel import select

from app.db.crud import BaseRepository
from app.db.models import Enterprise, FactoryInFactory, InspectionRecord, Material
from app.services.svc_Import.import_parser import locate_header, normalize_name, split_hazards
from app.services.svc_Ledger.enterprise_service import (
    EnterpriseError,
    _BOOL_FIELDS,
    create_enterprise,
    enterprises,
    update_enterprise,
)


# ── 业务清洗规则（仅 EnterpriseWriter 使用）─────────────────────
# 设计取舍：这些规则针对"企业监管"业务的台账 Excel，由 L3 _clean_fields
# 调用，不污染 L4 通用解析层（import_parser 保持业务无关）。
# 规则性质：补全/剥离，不做强制覆盖（除电话/厂房编号是从混合字段剥离）。

# 手机号 / 座机：用于从"姓名 电话"混合字段拆出电话
_PHONE_RE = re.compile(r'(1[3-9]\d{9}|0\d{2,3}-?\d{7,8})')

# 企业名末尾的厂房编号（如 #9#10# / #12-15 / #3、4）：→ 剥离到 extra.厂房编号
_BUILDING_NO_RE = re.compile(r'(.+?)(#[0-9#\-、，,]+)$')

# 企业名 → 行业关键词推断（专有词在前，避免"汽车"误匹配"汽车城"先于"汽修"）
_NAME_INDUSTRY_RULES: tuple[tuple[str, str], ...] = (
    ("物流", "物流"),
    ("餐饮", "餐饮"),
    ("咖啡", "餐饮"),
    ("建材", "建材"),
    ("石材", "建材"),
    ("汽修", "汽车服务"),
    ("汽车服务", "汽车服务"),
    ("汽车城", "汽车及零配件销售店"),
    ("叉车", "设备租赁"),
    ("商贸", "商贸"),
    ("贸易", "商贸"),
    ("食品", "食品"),
    ("机械", "机械"),
    ("化工", "化工"),
    ("纺织", "纺织"),
    ("电子", "电子"),
)


# ── L2：字段规格 + 合并函数 ───────────────────────────────────

_SKIP = object()  # 哨兵：merge 结果为"跳过该字段"


def _is_blank(v: Any) -> bool:
    """None / 空串 / 空数组视为空值。"""
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, tuple, dict)) and len(v) == 0:
        return True
    return False


@dataclass(frozen=True)
class FieldSpec:
    """字段规格：三个正交维度，合并策略由维度 1+2 推导。

    维度1  is_identity    主键 vs 其他     -> 主键更新时 SKIP
    维度2  data_type      string|value|array|string_array -> 推导合并策略（见 merge_field）
    维度3  audit_weight   important|general -> 日志/报告权重（风险类型/企业类型变更=important）

    合并策略推导表（分类1 x 分类2）：
      主键 + 任意         -> SKIP
      其他 + string       -> 追加（默认）/ 覆盖（override=True，上级决定）
      其他 + value        -> 后来值（新值非空就用，空保留旧值）
      其他 + array        -> key 级合并（只增不删）
      其他 + string_array -> list 级 union（去重保序，只增不删，旧值在前）
    """

    is_identity: bool = False
    data_type: str = "string"        # string | value | array | string_array
    audit_weight: str = "general"    # important | general
    allowed: tuple = ()              # 枚举值域（string/string_array 且需校验时）


def merge_field(spec: FieldSpec, current: Any, incoming: Any, *, override: bool = False) -> Any:
    """从分类 1+2 推导合并策略，返回写入值或 _SKIP。

    override 参数仅影响 string 类型：
      False（默认）= 追加：新值非空才写，但不强制覆盖已有非空旧值（两者非空不同时才覆盖）
      True  = 覆盖：新值非空就覆盖旧值（即使旧值也非空也替换）

    value/array 类型不受 override 影响。
    """
    if spec.is_identity:
        return _SKIP

    if spec.data_type == "string":
        if _is_blank(incoming):
            return _SKIP
        if spec.allowed:
            for a in spec.allowed:
                if a in str(incoming):
                    return a
            return _SKIP
        if not override and _is_blank(current):
            return incoming
        if not override and current == incoming:
            return _SKIP
        # 子串防退化（仅非 override）：新旧都非空且不同时，
        # 新值是旧值子串（旧值信息更全，如"王彬 18068890557" vs "王彬"）→ 保留旧值；
        # 旧值是新值子串（新值更全）→ 用新值；两者无关 → 维持原有覆盖语义。
        if not override:
            cur_s, inc_s = str(current), str(incoming)
            if inc_s in cur_s:
                return _SKIP
            if cur_s in inc_s:
                return incoming
        return incoming

    if spec.data_type == "value":
        if _is_blank(incoming):
            return _SKIP
        if isinstance(incoming, bool):
            return bool(incoming)
        try:
            return int(incoming)
        except (TypeError, ValueError):
            return _SKIP

    if spec.data_type == "array":
        merged = dict(current or {})
        for k, v in (incoming or {}).items():
            if not _is_blank(v):
                merged[k] = v
        return merged

    if spec.data_type == "string_array":
        # list 级 union：去重保序，旧值在前，新值不在旧值里的追加
        if _is_blank(incoming):
            return _SKIP
        inc_list = incoming if isinstance(incoming, (list, tuple)) else [incoming]
        # allowed 过滤：把 incoming 映射到 canonical 枚举值（子串匹配，兼容 Excel 变体）
        if spec.allowed:
            inc_list = [a for a in spec.allowed
                        if any(a in str(v) for v in inc_list)]
        if not inc_list:
            return _SKIP
        # 旧值归一为 list（None/空 → []，单值 → [v]）
        if _is_blank(current):
            cur_list = []
        elif isinstance(current, (list, tuple)):
            cur_list = list(current)
        else:
            cur_list = [current]
        # union 去重保序
        seen = set(cur_list)
        merged = list(cur_list)
        for v in inc_list:
            if v not in seen:
                merged.append(v)
                seen.add(v)
        return merged

    return _SKIP


# ── 写入/预览结果 ──────────────────────────────────────────────
@dataclass
class WriteResult:
    """commit 结果：action in created / updated / skipped / failed。"""

    action: str
    obj_id: Optional[int] = None
    name: str = ""
    message: str = ""
    changes: dict = field(default_factory=dict)
    important_changes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "id": self.obj_id,
            "name": self.name,
            "message": self.message,
            "important_changes": self.important_changes,
        }


@dataclass
class PreviewResult:
    """preview 结果：resolve + compute_changes(L2)，不写库。

    agent 在 preview 和 commit 之间有控制权：
    审阅 changes / important_changes / errors，逐企业决策是否 commit。
    """

    action: str               # created / updated / skipped / failed
    obj_id: Optional[int] = None
    name: str = ""
    matched_field: str = ""    # 命中的身份键字段（code/name/""）
    changes: dict = field(default_factory=dict)               # L2 merge 后的全部字段变更
    important_changes: dict = field(default_factory=dict)     # 维度3 important 子集
    errors: str = ""           # 失败原因（action=failed 时）
    # preview 专属：每个字段的变化明细（旧值->新值），供 agent 审阅
    field_details: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "id": self.obj_id,
            "name": self.name,
            "matched_field": self.matched_field,
            "changes": self.changes,
            "important_changes": self.important_changes,
            "errors": self.errors,
            "field_details": self.field_details,
        }


# ── L3：编排层（TableWriter）──────────────────────────────────
class TableWriter:
    """每张 SQL 表一个子类：声明身份键 + field_specs。

    L3 拆分（preview/commit 模式）：
      preview(identity, fields) -> PreviewResult   resolve + L2 merge，不写库
      commit(identity, fields)  -> WriteResult      L1 write_fields 落库
      upsert(...)               -> WriteResult      preview + commit 一步到位（便捷）

    preview/commit 让 agent 在 L3 和 L2 之间有控制权：
      preview 返回每个字段的旧值->新值，agent 审阅后决定是否 commit。
    """

    repo: BaseRepository
    identity_fields: tuple = ()
    field_specs: dict = {}  # {字段名: FieldSpec}
    # L4 建议规则（可选，子类按需声明）：
    #   sheet_tag_rules: [(关键词, {field, value})]  Sheet 名 → 自动标签
    #   col_map_rules:   [(关键词, 目标字段)]          Excel 列名 → 建议映射
    sheet_tag_rules: list = ()
    col_map_rules: list = ()

    # 多 sheet 工作簿导入计划（MCP 工具 parse_workbook）用的启发式词表。
    # 通用解析层 import_parser 不内置任何业务词表；需要按 sheet 名/中文表头
    # 做"实体列识别 / 关系表判定 / 跳过判定 / 标签冲突提示"的子类在此声明，
    # 解析器经 workbook_plan(hints=...) 注入。默认空 = 纯通用模式
    #（只依据 col_map_rules / sheet_tag_rules 出计划）。
    # 可选键：entity_keywords / strong_entity_keywords /
    #         skip_sheet_keywords / relation_sheet_keywords /
    #         tag_conflict_words / alias_pattern
    workbook_hints: dict = {}

    async def workbook_name_index(self) -> Optional[dict]:
        """parse_workbook 匹配率预估用的库内实体名称索引。

        返回 {"exact": set(名称), "names": list(名称)}；None = 不预估匹配率。
        基类默认不预估，有名称主键的表由子类覆写。
        """
        return None

    # ── L1：最小写入单元（默认走 repo，子类可覆写走 service）──
    async def write_fields(self, pk: Any, **fields) -> Any:
        """无条件写入一组字段，不判断。"""
        return await self.repo.update(pk, **fields)

    async def create_record(self, **fields) -> Any:
        """无条件创建，不判断。"""
        return await self.repo.create(**fields)

    # ── resolve ───────────────────────────────────────────────
    async def resolve(self, identity: dict) -> tuple[Optional[Any], Optional[str]]:
        """按 identity_fields 依序匹配，返回 (对象, 命中字段) 或 (None, None)。"""
        for f in self.identity_fields:
            val = identity.get(f)
            if _is_blank(val):
                continue
            obj = await self.repo.get_by(**{f: str(val).strip()})
            if obj:
                return obj, f
        return None, None

    # ── L2 调用：compute_changes ──────────────────────────────
    def compute_changes(self, current: Any, fields: dict, *, override: bool = False) -> tuple[dict, dict]:
        """遍历 fields 字典（不是 field_specs），逐个查 spec -> merge。

        驱动方向：agent 给了什么字段，就去问表"你有这个字段的规格吗？"
        没有 spec 的字段直接跳过。
        """
        important, general = {}, {}
        for f, incoming in fields.items():
            spec = self.field_specs.get(f)
            if spec is None:
                continue
            cur = getattr(current, f, None)
            value = merge_field(spec, cur, incoming, override=override)
            if value is not _SKIP and value != cur:
                if spec.audit_weight == "important":
                    important[f] = value
                else:
                    general[f] = value
        return important, general

    def _compute_field_details(self, current: Any, fields: dict, *, override: bool = False) -> list[dict]:
        """逐字段明细：旧值/新值/SKIP，供 preview 返回给 agent 审阅。"""
        details = []
        for f, incoming in fields.items():
            spec = self.field_specs.get(f)
            if spec is None:
                continue
            cur = getattr(current, f, None)
            value = merge_field(spec, cur, incoming, override=override)
            details.append({
                "field": f,
                "current": cur,
                "incoming": incoming,
                "merged": value if value is not _SKIP else None,
                "skipped": value is _SKIP,
                "audit_weight": spec.audit_weight,
            })
        return details

    def extract_create_fields(self, identity: dict, fields: dict) -> dict:
        """创建时合并 identity + fields，只保留声明且非空的字段。"""
        combined = {**identity, **fields}
        return {
            f: v for f, v in combined.items()
            if f in self.field_specs and not _is_blank(v)
        }

    # ── preview：resolve + L2，不写库 ─────────────────────────
    async def preview(self, identity: dict, fields: dict, *, override: bool = False) -> PreviewResult:
        """预览：resolve + compute_changes(L2)，不写库。

        agent 拿到 PreviewResult 后审阅 changes/important_changes/errors，
        决定是否调 commit。
        """
        name = str(identity.get(self.identity_fields[0], "")) if identity else ""
        try:
            obj, matched = await self.resolve(identity)
            if obj is None:
                # 将创建：列出要写入的字段
                create_fields = self.extract_create_fields(identity, fields)
                return PreviewResult(
                    action="created", name=name,
                    changes=create_fields,
                    field_details=[{"field": f, "current": None, "incoming": v,
                                     "merged": v, "skipped": False, "audit_weight": "general"}
                                    for f, v in create_fields.items()],
                )
            # 更新：逐字段 L2 merge
            important, general = self.compute_changes(obj, fields, override=override)
            changes = {**important, **general}
            details = self._compute_field_details(obj, fields, override=override)
            return PreviewResult(
                action="updated" if changes else "skipped",
                obj_id=obj.id, name=name, matched_field=matched or "",
                changes=changes, important_changes=important,
                field_details=details,
            )
        except Exception as e:  # noqa: BLE001
            return PreviewResult(action="failed", name=name, errors=str(e))

    # ── commit：L1 写库 ────────────────────────────────────────
    async def commit(self, identity: dict, fields: dict, *, override: bool = False) -> WriteResult:
        """落库：resolve + compute_changes + write_fields。

        通常在 agent 审阅 preview 结果后调用。
        也可直接调（等价于旧 upsert）。
        """
        name = str(identity.get(self.identity_fields[0], "")) if identity else ""
        try:
            obj, matched = await self.resolve(identity)
            if obj is None:
                create_fields = self.extract_create_fields(identity, fields)
                obj = await self.create_record(**create_fields)
                return WriteResult("created", obj.id, name, "新建")
            important, general = self.compute_changes(obj, fields, override=override)
            changes = {**important, **general}
            if not changes:
                return WriteResult("skipped", obj.id, name, "无变化")
            obj = await self.write_fields(obj.id, **changes)
            return WriteResult("updated", obj.id, name,
                               f"按 {matched} 匹配更新",
                               changes=changes, important_changes=important)
        except Exception as e:  # noqa: BLE001
            return WriteResult("failed", None, name, str(e))

    # ── 便捷：preview + commit 一步到位 ────────────────────────
    async def upsert(self, identity: dict, fields: dict, *, override: bool = False) -> WriteResult:
        """便捷方法：等价于 commit（preview + commit 一步到位）。"""
        return await self.commit(identity, fields, override=override)

    async def flag(self, identity: dict, field: str, value: Any) -> WriteResult:
        """打标签：找到已有记录，只写一个字段（不新建）。"""
        name = str(identity.get(self.identity_fields[0], "")) if identity else ""
        try:
            obj, matched = await self.resolve(identity)
            if obj is None:
                return WriteResult("failed", None, name, "记录不存在，请先导入主表")
            spec = self.field_specs.get(field)
            if spec is None:
                return WriteResult("failed", None, name, f"字段 {field} 未声明")
            merged = merge_field(spec, getattr(obj, field, None), value)
            if merged is _SKIP:
                return WriteResult("skipped", obj.id, name, "标签无变化")
            obj = await self.write_fields(obj.id, **{field: merged})
            important = {field: merged} if spec.audit_weight == "important" else {}
            return WriteResult("updated", obj.id, name,
                               f"按 {matched} 打标签 {field}={merged}",
                               changes={field: merged},
                               important_changes=important)
        except Exception as e:  # noqa: BLE001
            return WriteResult("failed", None, name, str(e))


# ── 企业表写入器 ──────────────────────────────────────────────
class EnterpriseWriter(TableWriter):
    """企业表 preview / commit / upsert / flag。

    维度1 身份键：code, name
    维度2 数据类型：string（文本/枚举）、value（bool/数值）、array（extra）
    维度3 审计权重：安全相关字段=important，联系方式/地址等=general
    """

    repo = enterprises
    identity_fields = ("code", "name")

    def __init__(self):
        # 名称匹配缓存：批量 resolve 时避免每企业全表扫。
        # 惰性加载（首次 resolve 时填充），存 {归一化名称: (企业对象, 原始名称)}
        self._name_cache: dict = {}
        self._cache_loaded: bool = False

    async def _load_name_cache(self) -> None:
        """一次性拉全量企业，构建归一化名称 → 企业 的索引。"""
        if self._cache_loaded:
            return
        items, total = await self.repo.list(page=1, page_size=100000)
        self._name_cache = {
            normalize_name(e.name): e for e in items if getattr(e, "name", None)
        }
        self._cache_loaded = True

    field_specs = {
        # ── 维度1：身份键 ──
        "name":  FieldSpec(is_identity=True),
        "code":  FieldSpec(is_identity=True),
        # ── 维度3 important：企业类型变更 + 风险类型变更 ──
        "enterprise_category": FieldSpec(data_type="string_array", audit_weight="important",
                                        allowed=("劳密", "厂中厂出租", "厂中厂承租", "小场所", "其他")),
        **{b: FieldSpec(data_type="value", audit_weight="important")
           for b in _BOOL_FIELDS},
        # ── 维度3 general：其余全部 ──
        "asset_attribute":   FieldSpec(data_type="string", allowed=("韩资", "台资", "其他")),
        "enterprise_scale":  FieldSpec(data_type="string", allowed=("规上", "规下")),
        "fire_level":        FieldSpec(data_type="string", allowed=("二级", "三级", "重点单位")),
        "confined_space":    FieldSpec(data_type="string_array", allowed=("地上", "地下")),
        "confined_space_count":   FieldSpec(data_type="string"),
        "confined_space_type":    FieldSpec(data_type="string"),
        "confined_space_workers": FieldSpec(data_type="string"),
        "confined_space_hazards": FieldSpec(data_type="string"),
        "confined_space_op_subject":    FieldSpec(data_type="string"),
        "confined_space_op_cycle":      FieldSpec(data_type="string"),
        "confined_space_fixed_workers": FieldSpec(data_type="string"),
        "hazchem_names":          FieldSpec(data_type="string"),
        "hazchem_op_subject":     FieldSpec(data_type="string"),
        "hazchem_major_hazard":   FieldSpec(data_type="string"),
        "hazchem_state":          FieldSpec(data_type="string"),
        "hazchem_op_cycle":       FieldSpec(data_type="string"),
        "hazchem_usage":          FieldSpec(data_type="string"),
        "hazchem_annual_usage":   FieldSpec(data_type="string"),
        "hazchem_storage":        FieldSpec(data_type="string"),
        "hazchem_reserve":        FieldSpec(data_type="string"),
        "hazchem_fixed_workers":  FieldSpec(data_type="string"),
        "industry":          FieldSpec(data_type="string"),
        "region":             FieldSpec(data_type="string"),
        "address":            FieldSpec(data_type="string"),
        "legal_person":       FieldSpec(data_type="string"),
        "person_in_charge":   FieldSpec(data_type="string"),
        "safety_officer":     FieldSpec(data_type="string"),
        "employee_count":       FieldSpec(data_type="value"),
        "annual_output_value":  FieldSpec(data_type="value"),
        "extra":  FieldSpec(data_type="array"),
    }

    # ── L4 建议规则（企业表专有）──
    # 注意：顺序即优先级（sheet 名含多个关键词时取首个命中）。
    # 专有名词必须排在宽泛词之前（如"焊机"先于"新能源"类词）。
    sheet_tag_rules = [
        ("消防安全重点单位", {"field": "fire_level", "value": "重点单位"}),
        ("有限空间", {"field": "confined_space", "value": None}),
        ("危化品", {"field": "has_hazardous_chemicals", "value": True}),
        ("危险品", {"field": "has_hazardous_chemicals", "value": True}),
        ("粉尘", {"field": "involves_dust_explosion", "value": True}),
        ("注塑", {"field": "is_injection_molding", "value": True}),
        ("天然气", {"field": "has_natural_gas", "value": True}),
        ("宿舍", {"field": "has_dormitory", "value": True}),
        ("焊机", {"field": "is_new_energy_welding", "value": True}),
        ("危废", {"field": "has_hazardous_waste", "value": True}),
        ("危险废物", {"field": "has_hazardous_waste", "value": True}),
        ("劳密", {"field": "enterprise_category", "value": "劳密"}),
        ("劳动密集", {"field": "enterprise_category", "value": "劳密"}),
    ]

    col_map_rules = [
        # ── 身份键 + 精确字段（先匹配，优先级最高）──
        ("统一社会信用代码", "code"),
        ("信用代码", "code"),
        ("企业名称", "name"),
        ("单位名称", "name"),
        # ── 人员电话（必须在通用"负责人"/"安全负责人"之前，避免子串误匹配）──
        ("主要负责人电话", "_merge_phone:person_in_charge"),
        ("安全负责人电话", "_merge_phone:safety_officer"),
        ("安全员电话", "_merge_phone:safety_officer"),
        ("法定代表人电话", "_merge_phone:legal_person"),
        ("法人电话", "_merge_phone:legal_person"),
        # ── 人员姓名（具体名先匹配，通用"负责人"兜底）──
        ("法定代表人", "legal_person"),
        ("法人", "legal_person"),
        ("安全负责人姓名", "safety_officer"),
        ("安全负责人", "safety_officer"),
        ("安全员姓名", "safety_officer"),
        ("安全员", "safety_officer"),
        ("主要负责人姓名", "person_in_charge"),
        ("主要负责人", "person_in_charge"),
        ("负责人", "person_in_charge"),
        # ── 规模信息 ──
        ("企业总人数", "employee_count"),
        ("从业人数", "employee_count"),
        ("人数", "employee_count"),
        # ── 企业属性 ──
        ("地址", "address"),
        # region 保持园区口径（如"新能源园区"），行政区列（区/县、所在县区）不映射，
        # 避免覆盖已有园区值。行政区信息如需保留，由调用方显式映射到 extra:行政区。
        ("行业（", "industry"),
        ("行业", "industry"),
        ("国名经济分类", "extra:国民经济分类"),
        ("规上/规下", "enterprise_scale"),
        ("规上", "enterprise_scale"),
        ("规模", "enterprise_scale"),
        # ── 有限空间 / 大型储罐 / 危化品等标签 ──
        # 「有限空间作业*」长名规则必须排在危化品的通用短名（作业周期/作业人员是否固定）
        # 之前，否则有限空间 sheet 的列会误映射到 hazchem_* 字段
        ("有限空间作业人员是否固定", "confined_space_fixed_workers"),
        ("有限空间作业周期", "confined_space_op_cycle"),
        ("有限空间类型", "confined_space_type"),
        ("有限空间数量", "confined_space_count"),
        ("有限空间作业人数", "confined_space_workers"),
        ("有限空间危险因素", "confined_space_hazards"),
        ("有限空间位置", "confined_space"),
        ("地上", "confined_space"),
        ("地下", "confined_space"),
        ("是否涉及有限空间", "confined_space"),
        ("有限空间", "extra:有限空间"),
        # 是否列（bool 判断）用"是否"前缀精确匹配；明细列（数量/介质/名称）不误入 bool
        ("是否使用危化品", "has_hazardous_chemicals"),
        ("使用危化品名称", "hazchem_names"),
        ("危化品名称", "hazchem_names"),
        ("作业主体", "hazchem_op_subject"),
        ("是否构成重大危险源", "hazchem_major_hazard"),
        ("是否重大危险源", "hazchem_major_hazard"),
        ("危化品状态", "hazchem_state"),
        ("作业周期", "hazchem_op_cycle"),
        ("危化品用途", "hazchem_usage"),
        ("年度使用量", "hazchem_annual_usage"),
        ("存储方式", "hazchem_storage"),
        ("危化品储量", "hazchem_reserve"),
        ("作业人员是否固定", "hazchem_fixed_workers"),
        ("是否涉及粉尘爆炸", "involves_dust_explosion"),
        ("是否涉及粉尘", "involves_dust_explosion"),
        ("是否使用天然气", "has_natural_gas"),
        ("是否注塑", "is_injection_molding"),
        ("注塑企业", "is_injection_molding"),
        # 大型储罐：Excel 列是描述文本（"2只2.5m³"），非"是否"布尔，进 extra 保留原文
        ("涉及大型储罐", "extra:涉及大型储罐"),
        # 明细列进 extra（粉尘种类等，不入 bool）
        ("粉尘种类", "extra:粉尘种类"),
        # ── 额外元数据（进 extra）──
        ("街道", "extra:街道"),
        ("所在县区", "extra:所在县区"),
        ("联系电话", "extra:联系电话"),
    ]

    # ── parse_workbook 启发式词表 ──────────────────────────────
    # 【业务专有】仅适用于"企业监管"业务的企业汇总 Excel
    #（DEFAULT_XLSX_PATH：各类企业情况汇总（2026）.xlsx 及同类工作簿）。
    # 这些词来自该工作簿真实的中文 sheet 名/表头习惯，不是通用 Excel 规则：
    # 换数据源、换表时应在对应 writer 另行声明，勿下沉到 import_parser。
    workbook_hints = {
        # 表头中可能承载企业名称的列（厂中厂/租赁表的承租方、出租方也是实体列）
        "entity_keywords": ("企业名称", "单位名称", "名称", "承租方", "出租方"),
        # 强实体列：只有命中它才说明 sheet 描述的是"企业自身"；
        # 不含此列但 sheet 名命中下方关系词 → 双实体关系表（review）
        "strong_entity_keywords": ("企业名称",),
        # sheet 名命中即跳过：账号密码表、WPS 系统保留表
        "skip_sheet_keywords": ("帐号", "账号", "密码", "WpsReserved"),
        # 租赁/从属关系信号：出租方与承租方是两个实体，不能并入企业属性
        "relation_sheet_keywords": ("租赁", "出租", "承租", "厂中厂"),
        # sheet 名同时含这些词时，首个命中的 sheet 标签可能误判（仅提示人工）
        "tag_conflict_words": (
            "宿舍", "危废", "劳动密集", "劳密", "冷库", "标准化", "较大风险",
            "租赁", "出租", "承租", "厂中厂",
        ),
    }

    async def workbook_name_index(self) -> dict:
        """企业名称索引（parse_workbook 预估每个 sheet 的精确/归一化/新建命中）。"""
        items, _ = await self.repo.list(page=1, page_size=500)
        names = sorted({
            getattr(e, "name", "").strip()
            for e in items if getattr(e, "name", None)
        })
        return {"exact": set(names), "names": names}

    # ── L1 覆写：走 service 校验 ──
    async def write_fields(self, pk: int, **fields) -> Enterprise:
        return await update_enterprise(pk, **fields)

    async def create_record(self, **fields) -> Enterprise:
        return await create_enterprise(**fields)

    # ── resolve 覆写：code 精确 → name 精确 → name 归一化（跨 sheet 同名异写）──
    async def resolve(self, identity: dict) -> tuple[Optional[Enterprise], Optional[str]]:
        """多级身份匹配：code 精确 → name 精确 → 归一化精确 → 归一化包含（相似）→ 多字段交叉验证。

        返回 (企业对象, 命中方式) 或 (None, None)。
        命中方式：code / name / name_normalized / name_similar / fields_cross_check
        """
        # 一级：code 精确（最可靠）
        code = str(identity.get("code") or "").strip().upper()
        if code:
            obj = await self.repo.get_by(code=code)
            if obj:
                return obj, "code"

        name = str(identity.get("name") or "").strip()
        if not name:
            return None, None

        # 二级：name 精确
        obj = await self.repo.get_by(name=name)
        if obj:
            return obj, "name"

        # 三级：归一化精确（空白/全半角括号差异）
        await self._load_name_cache()
        target = normalize_name(name)
        if target and target in self._name_cache:
            return self._name_cache[target], "name_normalized"

        # 四级：归一化包含匹配（简称/后缀差异，高置信才自动命中）
        if len(target) >= 6:
            sim = self._similar_name(target)
            if sim:
                return sim, "name_similar"

        # 五级：多字段交叉验证（名称差异较大，用地址/行业/代码辅助判断）
        cross = await self._cross_check(identity)
        if cross:
            return cross, "fields_cross_check"

        return None, None

    def _similar_name(self, target_norm: str) -> Optional[Enterprise]:
        """归一化后双向包含、长度差 ≤4、双方 ≥6 字，返回唯一候选（多候选则放弃）。"""
        candidates = []
        for n_norm, ent in self._name_cache.items():
            if len(n_norm) < 6:
                continue
            if abs(len(n_norm) - len(target_norm)) > 4:
                continue
            if n_norm in target_norm or target_norm in n_norm:
                candidates.append(ent)
        if len(candidates) == 1:
            return candidates[0]
        return None

    async def _cross_check(self, identity: dict) -> Optional[Enterprise]:
        """名称匹配失败时，用地址/行业/法人等辅助字段交叉验证。

        仅当恰好一条辅助字段精确命中、且无歧义时才返回，否则 None（交给人工/外部检索）。
        """
        addr = str(identity.get("address") or "").strip()
        industry = str(identity.get("industry") or "").strip()
        if not addr and not industry:
            return None
        hits = []
        items, _ = await self.repo.list(page=1, page_size=100000)
        for e in items:
            score = 0
            if addr and (e.address or "").strip() and addr == (e.address or "").strip():
                score += 2
            if industry and (e.industry or "").strip() and industry == (e.industry or "").strip():
                score += 1
            if score >= 2:
                hits.append(e)
        if len(hits) == 1:
            return hits[0]
        return None

    # ── 清洗 ──
    def _clean_identity(self, identity: dict) -> dict:
        """清洗身份键：去空白、code 转大写、名称剥离厂房号（#编号）以便匹配。"""
        clean = {}
        for f, v in identity.items():
            if f not in self.identity_fields:
                continue
            if isinstance(v, str):
                v = v.strip()
                if f == "code":
                    v = v.upper()
                elif f == "name":
                    # 名称末尾 #编号 → 剥离，避免"上海彩顺9#10#"匹配不到
                    m = _BUILDING_NO_RE.match(v)
                    if m and m.group(1).strip():
                        v = m.group(1).strip()
            clean[f] = v
        return clean

    def _clean_fields(self, fields: dict) -> dict:
        """清洗数据字段：去空白，只保留声明的字段，并执行业务清洗：
            1. 电话拆分：从人员字段提取电话到 extra.联系电话，人名部分保留
            2. 行业推断：industry 为空时按企业名关键词推断
            3. 厂房编号剥离：从 name 末尾剥离 #编号 到 extra.厂房编号 + 标记厂中厂

        所有规则都是"补全/剥离"性质，不覆盖已有非空值（除混合字段剥离）。
        """
        clean = {}
        for f, v in fields.items():
            if f not in self.field_specs:
                continue
            if isinstance(v, str):
                v = v.strip()
                if f == "code":
                    v = v.upper()
            clean[f] = v
        self._enrich_fields(clean)
        return clean

    def _enrich_fields(self, clean: dict) -> None:
        """业务清洗（in-place）：电话拆分、行业推断、厂房编号剥离+厂中厂标记。

        仅在 _clean_fields 阶段调用，前置原本由 /tmp 脚本做的事，避免
        入库后再回补。规则保守：只在字段为空时填充，不强制覆盖已有值。
        """
        extra = clean.get("extra")
        if not isinstance(extra, dict):
            extra = {}

        # 1. 电话拆分：人员字段含电话时，电话提取到 extra.联系电话，
        #    人名部分留在原字段；字段只有电话时移到 extra 并清空原字段
        phones: list[str] = []
        for person_field in ("person_in_charge", "safety_officer", "legal_person"):
            val = clean.get(person_field)
            if not isinstance(val, str) or not val:
                continue
            m = _PHONE_RE.search(val)
            if not m:
                continue
            phone = m.group(1)
            name_part = val.replace(phone, "").strip(" -:,，、")
            if name_part and name_part != val:
                clean[person_field] = name_part
                phones.append(phone)
            elif not name_part:
                # 字段只有电话，没有人名 → 移到 extra，清空原字段
                clean.pop(person_field, None)
                phones.append(phone)
        if phones and not extra.get("联系电话"):
            extra["联系电话"] = phones[0]

        # 2. 行业推断：industry 为空时按 name 关键词推断
        if not clean.get("industry"):
            name = clean.get("name")
            if isinstance(name, str) and name:
                for kw, ind in _NAME_INDUSTRY_RULES:
                    if kw in name:
                        clean["industry"] = ind
                        break

        # 3. 厂房编号剥离：name 末尾 #编号 → extra.厂房编号 + 标记厂中厂
        name = clean.get("name")
        if isinstance(name, str) and name:
            m = _BUILDING_NO_RE.match(name)
            if m:
                pure_name = m.group(1).strip()
                building_no = m.group(2).strip()
                if pure_name and pure_name != name:
                    clean["name"] = pure_name
                    if not extra.get("厂房编号"):
                        extra["厂房编号"] = building_no
                    # enterprise_category 为空时标记厂中厂承租（厂房编号是承租方典型特征）
                    if not clean.get("enterprise_category"):
                        clean["enterprise_category"] = ["厂中厂承租"]

        if extra:
            clean["extra"] = extra

    # ── preview 覆写：加预处理 + 缺码校验 ──
    async def preview(self, identity: dict, fields: dict, *, override: bool = False) -> PreviewResult:
        identity = self._clean_identity(identity)
        fields = self._clean_fields(fields)
        name = str(identity.get("name") or identity.get("code") or "")
        try:
            ent, matched = await self.resolve(identity)
            if ent is None:
                if _is_blank(identity.get("name")) and _is_blank(identity.get("code")):
                    return PreviewResult(action="failed", name=name, errors="新建缺少企业名称或信用代码")
                create_fields = self.extract_create_fields(identity, fields)
                return PreviewResult(
                    action="created", name=name,
                    changes=create_fields,
                    field_details=[
                        {"field": f, "current": None, "incoming": v,
                         "merged": v, "skipped": False, "audit_weight": "general"}
                        for f, v in create_fields.items()
                    ],
                )
            important, general = self.compute_changes(ent, fields, override=override)
            changes = {**important, **general}
            details = self._compute_field_details(ent, fields, override=override)
            return PreviewResult(
                action="updated" if changes else "skipped",
                obj_id=ent.id, name=ent.name, matched_field=matched or "",
                changes=changes, important_changes=important,
                field_details=details,
            )
        except EnterpriseError as e:
            return PreviewResult(action="failed", name=name, errors=str(e))
        except Exception as e:  # noqa: BLE001
            return PreviewResult(action="failed", name=name, errors=f"未预期失败：{e}")

    # ── commit 覆写：加预处理 + 缺码校验 ──
    async def commit(self, identity: dict, fields: dict, *, override: bool = False) -> WriteResult:
        identity = self._clean_identity(identity)
        fields = self._clean_fields(fields)
        name = str(identity.get("name") or identity.get("code") or "")
        try:
            ent, matched = await self.resolve(identity)
            if ent is None:
                if _is_blank(identity.get("name")) and _is_blank(identity.get("code")):
                    return WriteResult("failed", None, name, "新建缺少企业名称或信用代码")
                create_fields = self.extract_create_fields(identity, fields)
                obj = await self.create_record(**create_fields)
                return WriteResult("created", obj.id, obj.name, "新建")
            important, general = self.compute_changes(ent, fields, override=override)
            changes = {**important, **general}
            if not changes:
                return WriteResult("skipped", ent.id, ent.name, "无变化")
            obj = await self.write_fields(ent.id, **changes)
            return WriteResult("updated", obj.id, ent.name,
                               f"按 {matched} 匹配更新",
                               changes=changes, important_changes=important)
        except EnterpriseError as e:
            return WriteResult("failed", None, name, str(e))
        except Exception as e:  # noqa: BLE001
            return WriteResult("failed", None, name, f"未预期失败：{e}")

    # ── upsert = commit（便捷）──
    async def upsert(self, identity: dict, fields: dict, *, override: bool = False) -> WriteResult:
        return await self.commit(identity, fields, override=override)

    async def flag(self, identity: dict, field: str, value: Any) -> WriteResult:
        identity = self._clean_identity(identity)
        name = str(identity.get("name") or identity.get("code") or "")
        try:
            ent, matched = await self.resolve(identity)
            if ent is None:
                return WriteResult("failed", None, name, "企业不存在，请先导入主表")
            spec = self.field_specs.get(field)
            if spec is None:
                return WriteResult("failed", None, name, f"字段 {field} 未声明")
            merged = merge_field(spec, getattr(ent, field, None), value)
            if merged is _SKIP:
                return WriteResult("skipped", ent.id, ent.name, "标签无变化")
            obj = await self.write_fields(ent.id, **{field: merged})
            important = {field: merged} if spec.audit_weight == "important" else {}
            return WriteResult("updated", obj.id, ent.name,
                               f"按 {matched} 打标签 {field}={merged}",
                               changes={field: merged},
                               important_changes=important)
        except EnterpriseError as e:
            return WriteResult("failed", None, name, str(e))
        except Exception as e:  # noqa: BLE001
            return WriteResult("failed", None, name, f"未预期失败：{e}")


# ── 其余表写入器（骨架）──────────────────────────────────────
class MaterialWriter(TableWriter):
    """材料表写入骨架。材料正式导入走上传通道；本 writer 预留纯 SQL 场景。"""

    repo = BaseRepository(Material)
    identity_fields = ("uid",)
    field_specs = {
        "uid": FieldSpec(is_identity=True),
        "filename":          FieldSpec(),
        "file_hash":         FieldSpec(),
        "status":            FieldSpec(),
        "enterprise_codes":  FieldSpec(),
        "scope_industry":    FieldSpec(),
        "scope_region":      FieldSpec(),
    }


# ── 巡查隐患导入专用清洗（不进通用 parser）────────────────────
# Excel「是否已整改」原文 → SQL status 枚举（open/rectifying/closed）
_INSPECTION_STATUS_MAP: tuple[tuple[str, str], ...] = (
    ("已整改", "closed"), ("整改完成", "closed"), ("已闭环", "closed"), ("完成整改", "closed"),
    ("整改中", "rectifying"), ("正在整改", "rectifying"),
    ("未整改", "open"), ("待整改", "open"), ("未完成", "open"), ("未闭环", "open"),
)


def _parse_inspection_date(v: Any) -> Optional[date]:
    """Excel 日期单元格经 normalize_row 变成 '2026-09-01 00:00:00' 字符串 → date。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _map_inspection_status(v: Any) -> str:
    """状态中文原文 → open/rectifying/closed；识别不了默认 open。"""
    if v is None:
        return "open"
    s = str(v).strip()
    for kw, st in _INSPECTION_STATUS_MAP:
        if kw in s:
            return st
    return "open"


def _parse_inspection_datetime(v: Any) -> Optional[datetime]:
    """解析日期/日期时间字符串（JSON-RPC 通道日期会变成 '2026-02-03' / ISO 串）。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime.combine(v, datetime.min.time())
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ── 厂中厂责任清单 Excel 专用清洗（FactoryInFactoryWriter 用）─────────
def _split_person_phone(s: Any) -> tuple[Optional[str], Optional[str]]:
    """'张三 13812345678' / '张三 13812345678（备注）' → ('张三', '13812345678')。

    无电话时原样返回姓名；剥离括号备注后再匹配手机号/座机。
    """
    if s is None:
        return None, None
    s = str(s).strip()
    s = re.sub(r"[（(][^（）()]*[）)]?", "", s).strip()
    m = _PHONE_RE.search(s)
    if m:
        phone = m.group(0)
        name = (s[: m.start()] + s[m.end():]).strip().rstrip("：:、 ,，")
        return name or None, phone
    return s or None, None


def _parse_use_area(v: Any) -> Optional[str]:
    """出租方使用面积：数字（㎡）或文本（'纯出租'）统一返回字符串。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = str(v).strip()
    if not s or s in ("\\", "/", "、"):
        return None
    return s


def _parse_tenant_area(v: Any) -> Optional[float]:
    """承租面积：'120' / '120.5' / '1,200' → float。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("\n", "")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_agreement(v: Any) -> Optional[bool]:
    """'是/已签' → True，'否/未签' → False，识别不了 None。"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("是", "已签", "True", "true", "1"):
        return True
    if s in ("否", "未签", "False", "false", "0"):
        return False
    return None


def _parse_fif_date(v: Any) -> Optional[date]:
    """厂中厂备注里的排查时间：兼容 '2026.6.29' / '2026-06-29' / datetime。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    # 形如 2026.6.29 / 2026.08.13
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return _parse_inspection_date(s)



class InspectionWriter(TableWriter):
    """巡查隐患记录写入器（「总隐患汇总表」Excel 通道）。

    导入模型：
      Excel 一行 = 某企业一次检查 + 单元格内"1、…；2、…"多条隐患
      expand_skeletons 把一行拆成 N 个 skeleton（hazard_index 1..N），
      落库后一行一条隐患；前端按 inspection_batch + enterprise_id 分组显示。

    身份键（唯一约束 uq_inspection_hazard_identity）：
      (inspection_batch, enterprise_id, hazard_index)
      重复导入按身份键 resolve → 更新，不重复建档。

    企业关联：数据源锁死 Enterprise 主表（enterprise_name/enterprise_code
      冗余列已删除）。导入时按 Excel 企业名多级匹配主表；匹配不到自动建档
      （与 2026-10-16 存量清洗口径一致），保证 enterprise_id 100% 关联。
    """

    repo = BaseRepository(InspectionRecord)
    identity_fields = ("inspection_batch", "enterprise_id", "hazard_index")

    # 有中文表头的汇总 Sheet：列名关键词 → 目标字段（suggest_col_map 用）。
    # 序号/剩余整改数 不映射 → normalize_row 自动忽略。
    # *_raw 是仅 expand_skeletons 消费的临时键，不在 field_specs 中，
    # normalize_row 会按纯文本原样保留在 skeleton 上。
    col_map_rules = [
        ("序号", "seq_no"),
        ("企业", "enterprise_name"),
        ("隐患内容", "hazard_description"),
        ("检查时间", "inspection_date"),
        ("检查人", "inspectors"),
        ("整改责任人", "rect_responsible_name"),
        ("发现隐患数", "hazard_count_total"),
        ("是否已整改", "status_raw"),
        ("整改情况", "rect_note_raw"),
        ("整改时间", "rect_completed_at_raw"),
        ("整改期限", "rect_deadline_days_raw"),
        ("园区", "region"),
        ("行业", "industry"),
        ("备注", "remark"),
    ]

    # 无表头的月度 Sheet（如「2026年9月隐患」）：列字母 → 目标字段。
    # 实测列序（2026-09 sheet，openpyxl 探测）：
    # A 序号 B 园区 C 企业 D 隐患内容 E 检查时间 F 检查人 G 整改责任人
    # H 发现隐患数 I 是否已整改 J 整改情况(文本) K 剩余整改数
    # L 整改期限(日期) M 期限天数 N 行业
    # 注意与年度汇总表的差异：月度表 J 是说明文本而非完成时间、
    # L 是期限日期（年度表 L 是整改完成时间、M 是"30天"文本），
    # 月度表没有实际完成时间列，rect_completed_at 一律留空。
    positional_col_map = {
        "A": "seq_no",
        "B": "region",
        "C": "enterprise_name",
        "D": "hazard_description",
        "E": "inspection_date",
        "F": "inspectors",
        "G": "rect_responsible_name",
        "H": "hazard_count_total",
        "I": "status_raw",
        "J": "rect_note_raw",
        "L": "rect_deadline_raw",
        "N": "industry",
    }
    headerless_keywords = ("月隐患",)

    field_specs = {
        # ── 身份键 ──
        "inspection_batch": FieldSpec(is_identity=True),
        "hazard_index":     FieldSpec(is_identity=True, data_type="value"),
        # ── 巡查共享信息 ──
        "region":             FieldSpec(),
        "industry":           FieldSpec(),
        "inspection_date":    FieldSpec(),
        "inspectors":         FieldSpec(),
        "hazard_count_total": FieldSpec(data_type="value"),
        # ── 隐患（每行一条；level/category 导入留空，agent 后续补）──
        "hazard_description": FieldSpec(audit_weight="important"),
        "hazard_level":       FieldSpec(audit_weight="important",
                                        allowed=("low", "medium", "high")),
        "hazard_category":    FieldSpec(audit_weight="important"),
        "title":              FieldSpec(),
        "location":           FieldSpec(),
        # ── 整改 ──
        "rect_responsible_name": FieldSpec(),
        "rect_deadline":         FieldSpec(),
        "rect_completed_at":     FieldSpec(),
        "status":                FieldSpec(allowed=("closed", "rectifying", "open")),
        "remark":                FieldSpec(),
    }

    # ── Sheet 形态判定 ────────────────────────────────────────
    def is_headerless(self, sheet_name: str) -> bool:
        """月度明细 Sheet（如「2026年9月隐患」）无表头，首行即数据。"""
        return any(kw in sheet_name for kw in self.headerless_keywords)

    # ── L4 钩子：一行多隐患拆分 + 批次/状态/期限派生（纯解析，不碰 DB）──
    def expand_skeletons(self, skeletons: list[dict], sheet_name: str) -> list[dict]:
        """normalize_row 产物（一行一 skeleton）→ 拆分后的一行一隐患 skeleton。

        - 竖向合并单元格导致企业名为空的行，归属上一企业；
        - inspection_batch：月度 Sheet 取「年-月」（2026-09），
          汇总表按检查日期取 INS-YYYYMMDD（同日多企业共享批次）；
        - status：中文原文映射 open/rectifying/closed；
        - rect_deadline：优先取 Excel 明确期限日期（月度表 L 列）；
          否则按检查时间 + 整改期限天数（年度表 M 列"30天"，缺省 30 天）；
        - rect_completed_at：仅已整改记录、且表中有「整改时间」列时取
          （年度汇总表有；月度明细表无此列，留空）；
        - batch：序号-检查日期（如 "299-2026-09-01"）；无序号或日期时留空待补。
        """
        expanded: list[dict] = []
        last_name = ""

        for sk in skeletons:
            name = str(sk.get("enterprise_name") or "").strip()
            if name:
                last_name = name
            elif last_name:
                # 竖向合并单元格场景：企业名空行归属上一企业
                sk["enterprise_name"] = last_name

            hazards = split_hazards(sk.get("hazard_description"))
            if not hazards:
                continue

            insp_date = _parse_inspection_date(sk.get("inspection_date"))
            seq = str(sk.get("seq_no") or "").strip()
            batch = (
                f"{seq}-{insp_date.strftime('%Y-%m-%d')}"
                if seq and insp_date else None
            )

            # 状态：I 列原文 → 枚举（先留原文，用于和 J 列说明去重）
            status_text = str(sk.get("status_raw") or "").strip()
            status = _map_inspection_status(sk.pop("status_raw", None))

            # J「整改情况」说明文本：与状态原文重复的不当备注，
            # 有实质内容（如"六化建设未完成"）并入 remark
            note = sk.pop("rect_note_raw", None)
            note = str(note).strip() if note not in (None, "") else ""
            if note and note != status_text:
                old_remark = str(sk.get("remark") or "").strip()
                sk["remark"] = note if not old_remark else f"{old_remark}；{note}"

            # 整改期限：统一按 检查日期 + 30 天（Excel 「整改期限」列仅作信息参考，
            # 不作为落库 deadline 的依据，避免手工填错导致期限过短）；
            # 月度表 L 列如果给了明确日期则优先用之（与历史口径兼容）。
            deadline = _parse_inspection_date(sk.pop("rect_deadline_raw", None))
            if deadline is None and insp_date:
                deadline = insp_date + timedelta(days=30)
            sk.pop("rect_deadline_days_raw", None)

            # 实际完成时间：仅 closed 且来源列可解析时写入（月度表无此列 → None）
            complete_raw = sk.pop("rect_completed_at_raw", None)
            completed_date = _parse_inspection_date(complete_raw)
            completed = (
                datetime.combine(completed_date, datetime.min.time())
                if completed_date and status == "closed" else None
            )

            # 隐患总数：Excel 值与实际拆出条数取较大者（Excel 少报时以内容为准，
            # Excel 多报时保留原值，提示 agent 可能有拆分遗漏）
            excel_total = sk.get("hazard_count_total")
            excel_total = int(excel_total) if isinstance(excel_total, int) else 0
            total = max(excel_total, len(hazards))

            sk.pop("hazard_description", None)
            sk["inspection_batch"] = batch
            sk["inspection_date"] = insp_date
            sk["status"] = status
            sk["hazard_count_total"] = total
            sk["rect_deadline"] = deadline
            sk["rect_completed_at"] = completed

            for i, h in enumerate(hazards, start=1):
                row = dict(sk)
                row["hazard_index"] = i
                row["hazard_description"] = h
                expanded.append(row)

        return expanded

    # ── resolve：三身份键同时等值匹配（基类是逐字段单匹配，不适用）──
    async def resolve(self, identity: dict) -> tuple[Optional[Any], Optional[str]]:
        filters = {
            f: identity[f] for f in self.identity_fields
            if not _is_blank(identity.get(f))
        }
        if not filters:
            return None, None
        obj = await self.repo.get_by(**filters)
        return (obj, "+".join(filters)) if obj else (None, None)

    # ── 企业主表匹配：精确名 → 归一化名 → 高置信相似名（复用 EnterpriseWriter）──
    # 公司形态后缀等价（仅隐患匹配用，不影响企业表自身的导入匹配）：
    # 有限公司 / 有限责任公司 / 股份有限公司 / 公司 去尾后比较主干
    _CORP_SUFFIXES = ("股份有限公司", "有限责任公司", "有限公司", "公司")
    # 括号注释：（金属熔融）（已关停）（曾用名：…）（奥迪）以及不配对容错（江苏)
    _PAREN_RE = re.compile(r"[（(][^（）()]*[）)]?")
    # 开头地级行政区划：盐城市XX → 盐城XX（兼容 Excel 与主表"市"字有无差异）
    _CITY_PREFIX_RE = re.compile(r"^([\u4e00-\u9fa5]{2,4}?)市")

    def _corp_stem(self, norm_name: str) -> str:
        for suf in self._CORP_SUFFIXES:
            if norm_name.endswith(suf):
                return norm_name[: -len(suf)]
        return norm_name

    def _strip_parens(self, s: str) -> str:
        """剥离名称中的括号注释块（含全半角、不配对容错）。"""
        prev = None
        cur = s
        while prev != cur:
            prev = cur
            cur = self._PAREN_RE.sub("", cur)
        return cur

    def _norm_city(self, s: str) -> str:
        """去掉开头地名中的"市"：盐城市杰旺→盐城杰旺；江苏市X 类不常见，仅首个匹配。"""
        return self._CITY_PREFIX_RE.sub(r"\1", s, count=1)

    async def _stripped_name_index(self) -> list:
        """主表企业名剥括号/归一化后的 [(stem_or_full, ent)] 索引（懒加载缓存）。"""
        cache = getattr(self, "_stripped_cache", None)
        if cache is not None:
            return cache
        await enterprise_writer._load_name_cache()
        cache = [
            (self._strip_parens(n_norm), ent)
            for n_norm, ent in enterprise_writer._name_cache.items()
        ]
        self._stripped_cache = cache
        return cache

    def _unique_contain_candidate(self, target: str, index: list) -> Optional[Enterprise]:
        """target 与索引项双向包含、长度差 ≤3、短串 ≥6 字，唯一候选才命中。"""
        if len(target) < 6:
            return None
        cands = []
        for n, ent in index:
            if len(n) < 6 or abs(len(n) - len(target)) > 3:
                continue
            if n in target or target in n:
                cands.append(ent)
        return cands[0] if len(cands) == 1 else None

    async def _resolve_enterprise(self, name: str) -> Optional[Enterprise]:
        if not name:
            return None
        ent = await enterprises.get_by(name=name)
        if ent:
            return ent
        nn = normalize_name(name)
        if not nn:
            return None
        await enterprise_writer._load_name_cache()
        ent = enterprise_writer._name_cache.get(nn)
        if ent:
            return ent
        # 高置信相似兜底：处理"…有限公司园区"后缀、长度差 ≤4 的唯一候选
        if len(nn) >= 6:
            ent = enterprise_writer._similar_name(nn)
            if ent:
                return ent
        # 公司后缀等价：如"…机械有限公司"（Excel）vs"…机械有限责任公司"（主表）
        stem = self._corp_stem(nn)
        if stem and len(stem) >= 4:
            candidates = [
                e for n, e in enterprise_writer._name_cache.items()
                if self._corp_stem(n) == stem
            ]
            if len(candidates) == 1:
                return candidates[0]
        # 剥括号注释后再匹配（主表侧常见"XX有限公司（金属熔融）/（已关停）/
        # （曾用名：…）"，Excel 侧也可能有（奥迪）等品牌注记）
        stripped = normalize_name(self._strip_parens(nn))
        if stripped:
            index = await self._stripped_name_index()
            # 剥括号后精确 / 公司主干精确
            exact = [e for n, e in index if n == stripped]
            if len(exact) == 1:
                return exact[0]
            s_stem = self._corp_stem(stripped)
            if s_stem and len(s_stem) >= 4:
                stem_cands = [e for n, e in index if self._corp_stem(n) == s_stem]
                if len(stem_cands) == 1:
                    return stem_cands[0]
                # 去后缀后双向包含（程荣模塑料/程荣模塑、百悦机械/机械制造）
                stem_index = [(self._corp_stem(n), e) for n, e in index]
                ent = self._unique_contain_candidate(s_stem, stem_index)
                if ent:
                    return ent
                # 城市词归一后再比（盐城/盐城市差异：杰旺/树人/俣愿/盛世）
                city_target = self._norm_city(s_stem)
                city_index = [
                    (self._norm_city(self._corp_stem(n)), e) for n, e in index
                ]
                city_cands = [e for cn, e in city_index if cn == city_target]
                if len(city_cands) == 1:
                    return city_cands[0]
                ent = self._unique_contain_candidate(city_target, city_index)
                if ent:
                    return ent
        return None

    async def _enterprise_pointer(self, name: str) -> dict:
        """按名称解析企业指针；匹配不到自动建档（保证 FK 100% 关联）。"""
        ent = await self._resolve_enterprise(name)
        if ent is None and str(name).strip():
            # 直接走 repo（create_enterprise 服务强制要求 code，导入场景没有）
            ent = await enterprise_writer.repo.create(name=str(name).strip())
            # 同步名称缓存，避免同一批导入重复建档
            if getattr(enterprise_writer, "_cache_loaded", False):
                enterprise_writer._name_cache[normalize_name(str(name).strip())] = ent
                self._stripped_cache = None
        if not ent:
            return {}
        return {"enterprise_id": ent.id}

    async def suggest_enterprise(self, name: str, threshold: float = 0.8) -> Optional[dict]:
        """未匹配企业的疑似候选：剥括号+城市词+去后缀归一后 difflib top1。

        仅用于人工提示，不自动回填。返回 {name, score} 或 None。
        """
        import difflib
        if not name:
            return None
        index = await self._stripped_name_index()
        target = self._norm_city(self._corp_stem(
            normalize_name(self._strip_parens(normalize_name(name)))
        ))
        if not target:
            return None
        best, best_score = None, 0.0
        for n, ent in index:
            cand = self._norm_city(self._corp_stem(n))
            if not cand:
                continue
            score = difflib.SequenceMatcher(None, target, cand).ratio()
            if score > best_score:
                best, best_score = ent, score
        if best and best_score >= threshold:
            return {"name": best.name, "score": round(best_score, 2)}
        return None

    # ── 入参类型强转（MCP 走 JSON-RPC，date/datetime/int 会退化成字符串）──
    def _coerce_payload(self, identity: dict, fields: dict) -> None:
        hi = identity.get("hazard_index")
        if isinstance(hi, str) and hi.strip().lstrip("-").isdigit():
            identity["hazard_index"] = int(hi.strip())
        for f in ("inspection_date", "rect_deadline"):
            v = fields.get(f)
            if isinstance(v, str) and v.strip():
                parsed = _parse_inspection_date(v)
                if parsed is not None:
                    fields[f] = parsed
        v = fields.get("rect_completed_at")
        if isinstance(v, str) and v.strip():
            parsed = _parse_inspection_datetime(v)
            if parsed is not None:
                fields["rect_completed_at"] = parsed
        for f in ("hazard_index", "hazard_count_total", "enterprise_id",
                  "inspector_user_id", "rect_responsible_user_id"):
            v = fields.get(f)
            if isinstance(v, str) and v.strip().lstrip("-").isdigit():
                fields[f] = int(v.strip())

    # ── preview：resolve 隐患身份键 + 解析企业指针（只读，不写库）──
    async def preview(self, identity: dict, fields: dict, *, override: bool = False) -> PreviewResult:
        identity, fields = dict(identity), dict(fields)
        self._coerce_payload(identity, fields)
        name = str(identity.get("enterprise_name", ""))
        try:
            pointer = await self._enterprise_pointer(name)
            if not pointer:
                return PreviewResult(action="failed", name=name,
                                     errors=f"企业名为空，无法定位企业（Excel 行的企业列缺失）")
            identity["enterprise_id"] = pointer["enterprise_id"]
            obj, matched = await self.resolve(identity)
            if obj is None:
                create_fields = self.extract_create_fields(identity, fields)
                create_fields.update(pointer)
                details = [
                    {"field": f, "current": None, "incoming": v,
                     "merged": v, "skipped": False, "audit_weight": "general"}
                    for f, v in create_fields.items()
                ]
                return PreviewResult(
                    action="created", name=name, changes=create_fields,
                    field_details=details,
                )
            important, general = self.compute_changes(obj, fields, override=override)
            changes = {**important, **general}
            details = self._compute_field_details(obj, fields, override=override)
            return PreviewResult(
                action="updated" if changes else "skipped",
                obj_id=obj.id, name=name, matched_field=matched or "",
                changes=changes, important_changes=important,
                field_details=details,
            )
        except Exception as e:  # noqa: BLE001
            return PreviewResult(action="failed", name=name, errors=str(e))

    # ── commit：企业指针解析（必要时自动建档）+ 落库 ──────────
    async def commit(self, identity: dict, fields: dict, *, override: bool = False) -> WriteResult:
        identity, fields = dict(identity), dict(fields)
        self._coerce_payload(identity, fields)
        name = str(identity.get("enterprise_name", ""))
        try:
            pointer = await self._enterprise_pointer(name)
            if not pointer:
                return WriteResult("failed", None, name, "企业名为空，无法定位企业")
            identity["enterprise_id"] = pointer["enterprise_id"]
            obj, matched = await self.resolve(identity)
            if obj is None:
                # 整改责任人缺失时兜底：优先企业安全员，其次企业负责人
                if not str(fields.get("rect_responsible_name") or "").strip():
                    ent = await enterprises.get(pointer["enterprise_id"])
                    if ent:
                        fallback = (
                            str(ent.safety_officer or "").strip()
                            or str(ent.person_in_charge or "").strip()
                        )
                        if fallback:
                            fields["rect_responsible_name"] = fallback
                create_fields = self.extract_create_fields(identity, fields)
                create_fields.update(pointer)
                new_obj = await self.create_record(**create_fields)
                return WriteResult("created", new_obj.id, name, "新建")
            important, general = self.compute_changes(obj, fields, override=override)
            changes = {**important, **general}
            if not changes:
                return WriteResult("skipped", obj.id, name, "无变化")
            obj = await self.write_fields(obj.id, **changes)
            return WriteResult("updated", obj.id, name, f"按 {matched} 匹配更新",
                               changes=changes, important_changes=important)
        except Exception as e:  # noqa: BLE001
            return WriteResult("failed", None, name, str(e))


# ── 厂中厂专项整治记录写入器 ─────────────────────────────────
class FactoryInFactoryWriter(TableWriter):
    """厂中厂专项整治记录写入器（「厂中厂消防安全专项整治责任清单」Excel 通道）。

    导入模型：
      Excel 一行 = 一对（出租方, 承租方）+ 该次专项整治的隐患与整改。
      承租单位列（H 列）竖向合并单元格界定每条"承租方记录"边界；
      出租方/共享字段取每组首行（合并单元格 forward-fill），隐患跨行收集
      去重后换行拼接。出租方/承租方按名称匹配企业主表回填 *_enterprise_id
      （复用 InspectionWriter 的多级归一化匹配；匹配不到自动建档，
      与 2026-10-16 存量清洗口径一致）。lessor/tenant_name 与联系人
      列是 Enterprise 主表数据源（冗余列已删除），Excel 原文仅用于
      名称匹配，不落库。

    身份键：(inspection_batch, tenant_enterprise_id) —— 同批次同承租方
      重复导入 → 更新，不重复建档（与 scripts/import_factory_in_factory.py
      的幂等口径一致；tenant_name 解析为 enterprise_id 后参与匹配）。
    """

    repo = BaseRepository(FactoryInFactory)
    enterprises_repo = BaseRepository(Enterprise)
    identity_fields = ("inspection_batch", "tenant_enterprise_id")

    # 列位置（与原始责任清单 Excel 一致，1-based）
    _COLS = {
        "region": 1, "address": 2, "lessor_name": 3, "lessor_contact": 4,
        "lessor_use_area": 5, "lessor_hazard": 6, "lessor_rect_status": 7,
        "tenant_name": 8, "tenant_area": 9, "tenant_contact": 10,
        "agreement_signed": 11, "tenant_hazard": 12, "tenant_rect_status": 13,
        "remark": 14,
    }
    # 表头行 H 列出现的标题词（非数据，按包含匹配跳过；真实承租方为企业名，不含这些词）
    _HEADER_H_WORDS = ("承租单位", "承租人")

    # 备注里的字段解析正则
    _RE_SITE_CAT = re.compile(r"场所类别[:：]([^；;]+)")
    _RE_INSPECT_DATE = re.compile(r"排查时间[:：]([^；;]*)")
    _RE_INSPECTORS = re.compile(r"排查人员[:：]([^；;]+)")
    _RE_SUPERVISOR = re.compile(r"督改人员[:：]([^；;]+)")

    field_specs = {
        # ── 身份键 ──
        "inspection_batch": FieldSpec(is_identity=True),
        # ── 企业指针（commit 时按名称匹配/自动建档回填，不走 L2 merge）──
        "lessor_enterprise_id": FieldSpec(data_type="value"),
        "tenant_enterprise_id": FieldSpec(data_type="value"),
        # ── 出租方 ──
        "lessor_use_area":           FieldSpec(),
        "lessor_hazard_description": FieldSpec(audit_weight="important"),
        "lessor_rect_status":        FieldSpec(),
        # ── 承租方 ──
        "tenant_area":               FieldSpec(),  # float，按字符串通道透传
        "agreement_signed":          FieldSpec(data_type="value"),
        "tenant_hazard_description": FieldSpec(audit_weight="important"),
        "tenant_rect_status":        FieldSpec(),
        # ── 共享/排查信息 ──
        "region":          FieldSpec(),
        "address":         FieldSpec(),
        "site_category":   FieldSpec(),
        "inspection_date": FieldSpec(),
        "inspectors":      FieldSpec(),
        "rect_supervisor": FieldSpec(),
        "remark":          FieldSpec(),
    }

    def parse_sheet(self, ws, *, inspection_batch: str = "2026-厂中厂消防") -> list[tuple[dict, dict]]:
        """解析厂中厂责任清单 Sheet → [(identity, fields), ...]。

        承租单位列（H=8）竖向合并单元格界定每条"承租方记录"边界；
        出租方/共享字段取每组首行（合并单元格 forward-fill）；隐患跨行收集
        去重后换行拼接；备注里的场所类别/排查时间/排查人/督改人用正则提取。
        """
        # 合并单元格 forward-fill 查找表
        merge_lookup: dict[tuple[int, int], Any] = {}
        for mr in ws.merged_cells.ranges:
            topval = ws.cell(mr.min_row, mr.min_col).value
            for r in range(mr.min_row, mr.max_row + 1):
                for c in range(mr.min_col, mr.max_col + 1):
                    merge_lookup[(r, c)] = topval

        def val(r, c):
            if (r, c) in merge_lookup:
                return merge_lookup[(r, c)]
            return ws.cell(r, c).value

        def clean(v):
            if v is None:
                return None
            if isinstance(v, str):
                s = v.replace("　", " ").strip()
                if s in ("\\", "/", "、", "", "None"):
                    return None
                return s or None
            return v

        C = self._COLS
        # 定位表头，数据从表头下一行开始（兼容顶部标题/二级表头）
        try:
            data_start = locate_header(ws) + 1
        except Exception:  # noqa: BLE001
            data_start = 1

        # 承租单位（H 列）合并段作为记录边界
        h_merges = sorted(
            [mr for mr in ws.merged_cells.ranges
             if mr.min_col == C["tenant_name"] and mr.max_col == C["tenant_name"]
             and mr.min_row >= data_start],
            key=lambda m: m.min_row,
        )
        covered: set[int] = set()
        tenant_groups: list[tuple[int, int, str]] = []
        for mr in h_merges:
            tv = clean(val(mr.min_row, C["tenant_name"]))
            if tv and not any(w in tv for w in self._HEADER_H_WORDS):
                tenant_groups.append((mr.min_row, mr.max_row, tv))
                for r in range(mr.min_row, mr.max_row + 1):
                    covered.add(r)
        # 非合并但有值的 H 列行
        for r in range(data_start, ws.max_row + 1):
            if r in covered:
                continue
            hv = clean(val(r, C["tenant_name"]))
            if hv and not any(w in hv for w in self._HEADER_H_WORDS):
                tenant_groups.append((r, r, hv))
        tenant_groups.sort()

        out: list[tuple[dict, dict]] = []
        for top, last, tname in tenant_groups:
            lp, lph = _split_person_phone(clean(val(top, C["lessor_contact"])))
            tp, tph = _split_person_phone(clean(val(top, C["tenant_contact"])))

            # 隐患跨行收集去重
            lessor_haz: list[str] = []
            tenant_haz: list[str] = []
            last_lh = last_th = None
            for r in range(top, last + 1):
                fv = clean(val(r, C["lessor_hazard"]))
                if fv and fv != last_lh:
                    lessor_haz.append(fv)
                if fv:
                    last_lh = fv
                lv = clean(val(r, C["tenant_hazard"]))
                if lv and lv != last_th:
                    tenant_haz.append(lv)
                if lv:
                    last_th = lv

            remark = clean(val(top, C["remark"]))
            site_category = inspectors = rect_supervisor = None
            inspection_date_raw = None
            if remark:
                m = self._RE_SITE_CAT.search(remark)
                site_category = m.group(1).strip() if m else None
                m = self._RE_INSPECT_DATE.search(remark)
                inspection_date_raw = m.group(1).strip() if m else None
                m = self._RE_INSPECTORS.search(remark)
                inspectors = m.group(1).strip() if m else None
                m = self._RE_SUPERVISOR.search(remark)
                rect_supervisor = m.group(1).strip() if m else None

            identity = {"inspection_batch": inspection_batch, "tenant_name": tname}
            fields = {
                "lessor_name": clean(val(top, C["lessor_name"])),
                "lessor_contact_person": lp,
                "lessor_contact_phone": lph,
                "lessor_use_area": _parse_use_area(clean(val(top, C["lessor_use_area"]))),
                "lessor_hazard_description": "\n".join(lessor_haz) if lessor_haz else None,
                "lessor_rect_status": clean(val(top, C["lessor_rect_status"])),
                "tenant_area": _parse_tenant_area(clean(val(top, C["tenant_area"]))),
                "tenant_contact_person": tp,
                "tenant_contact_phone": tph,
                "agreement_signed": _parse_agreement(clean(val(top, C["agreement_signed"]))),
                "tenant_hazard_description": "\n".join(tenant_haz) if tenant_haz else None,
                "tenant_rect_status": clean(val(top, C["tenant_rect_status"])),
                "region": clean(val(top, C["region"])),
                "address": clean(val(top, C["address"])),
                "site_category": site_category,
                "inspection_date": _parse_fif_date(inspection_date_raw),
                "inspectors": inspectors,
                "rect_supervisor": rect_supervisor,
                "remark": remark,
            }
            out.append((identity, fields))
        return out

    # ── resolve：双身份键同时等值匹配（基类逐字段单匹配不适用）──
    async def resolve(self, identity: dict) -> tuple[Optional[Any], Optional[str]]:
        filters = {
            f: identity[f] for f in self.identity_fields
            if not _is_blank(identity.get(f))
        }
        if not filters:
            return None, None
        obj = await self.repo.get_by(**filters)
        return (obj, "+".join(filters)) if obj else (None, None)

    async def _resolve_enterprise_id(self, name: str) -> Optional[int]:
        """按名称匹配企业主表；匹配不到自动建档（复用 InspectionWriter 指针逻辑）。"""
        pointer = await inspection_writer._enterprise_pointer(name)
        return pointer.get("enterprise_id")

    async def _resolve_side_pointers(
        self, identity: dict, fields: dict
    ) -> tuple[dict, dict]:
        """把 Excel 原文名称解析为 *_enterprise_id：承租方进 identity（身份键），
        出租方进 fields（指针）。返回处理后的 (identity, fields) 副本。
        """
        identity, fields = dict(identity), dict(fields)
        tname = str(identity.get("tenant_name") or fields.get("tenant_name") or "").strip()
        lname = str(fields.get("lessor_name") or "").strip()
        if tname:
            tid = await self._resolve_enterprise_id(tname)
            if tid is not None:
                identity["tenant_enterprise_id"] = tid
        if lname:
            lid = await self._resolve_enterprise_id(lname)
            if lid is not None:
                fields["lessor_enterprise_id"] = lid
        return identity, fields

    async def _tag_enterprise_role(
        self, ent_id: Optional[int], role: str
    ) -> bool:
        """给企业 enterprise_category 追加厂中厂角色标签。

        幂等防重（已有该标签直接跳过），且只追加不删除——不影响劳密/小场所
        等其他标签；同一企业既是出租方又是承租方时，由两次记录分别追加两标签。

        Args:
            ent_id: 企业主键；为空（记录未关联企业）直接跳过
            role: "厂中厂出租" / "厂中厂承租"
        Returns:
            是否实际追加了标签
        """
        if not ent_id:
            return False
        ent = await self.enterprises_repo.get(ent_id)
        if not ent:
            return False
        cats = list(ent.enterprise_category or [])
        if role in cats:
            return False
        cats.append(role)
        await self.enterprises_repo.update(ent_id, enterprise_category=cats)
        return True

    # ── preview：先解析企业指针再 resolve（只读，不写库）──
    async def preview(self, identity: dict, fields: dict, *, override: bool = False) -> PreviewResult:
        identity, fields = await self._resolve_side_pointers(identity, fields)
        name = str(fields.get("tenant_name") or identity.get("tenant_name") or "")
        try:
            obj, matched = await self.resolve(identity)
            if obj is None:
                create_fields = self.extract_create_fields(identity, fields)
                return PreviewResult(
                    action="created", name=name, changes=create_fields,
                    field_details=[{"field": f, "current": None, "incoming": v,
                                    "merged": v, "skipped": False, "audit_weight": "general"}
                                   for f, v in create_fields.items()],
                    errors="" if identity.get("tenant_enterprise_id") else f"承租方「{name}」未解析到企业主表",
                )
            important, general = self.compute_changes(obj, fields, override=override)
            # 企业指针不参与常规 merge：现值为空时回填，已有值永不覆盖
            pointer = {}
            for f in ("lessor_enterprise_id", "tenant_enterprise_id"):
                v = fields.get(f) if f == "lessor_enterprise_id" else identity.get(f)
                if v is not None and getattr(obj, f, None) is None:
                    pointer[f] = v
            general.update(pointer)
            changes = {**important, **general}
            details = self._compute_field_details(obj, fields, override=override)
            return PreviewResult(
                action="updated" if changes else "skipped",
                obj_id=obj.id, name=name, matched_field=matched or "",
                changes=changes, important_changes=important,
                field_details=details,
            )
        except Exception as e:  # noqa: BLE001
            return PreviewResult(action="failed", name=name, errors=str(e))

    # ── commit：名称解析指针 + 落库 + 自动打标 ─────────
    async def commit(self, identity: dict, fields: dict, *, override: bool = False) -> WriteResult:
        identity, fields = await self._resolve_side_pointers(identity, fields)
        tname = str(identity.get("tenant_name") or fields.get("tenant_name") or "")
        try:
            if not identity.get("tenant_enterprise_id"):
                return WriteResult("failed", None, tname,
                                   f"承租方「{tname}」未解析到企业主表（名称为空或建档失败）")

            obj, matched = await self.resolve(identity)
            if obj is None:
                create_fields = self.extract_create_fields(identity, fields)
                result_obj = await self.create_record(**create_fields)
                wr = WriteResult("created", result_obj.id, tname, "新建")
            else:
                # 企业指针不参与常规 merge：现值为空时回填，已有值（含手工关联）永不覆盖
                pointer = {}
                for f in ("lessor_enterprise_id", "tenant_enterprise_id"):
                    v = fields.pop(f, None) if f == "lessor_enterprise_id" else identity.get(f)
                    if getattr(obj, f, None) is None and v is not None:
                        pointer[f] = v

                important, general = self.compute_changes(obj, fields, override=override)
                general.update(pointer)
                changes = {**important, **general}
                if not changes:
                    result_obj = obj
                    wr = WriteResult("skipped", obj.id, tname, "无变化")
                else:
                    result_obj = await self.write_fields(obj.id, **changes)
                    wr = WriteResult("updated", result_obj.id, tname,
                                     f"按 {matched} 匹配更新",
                                     changes=changes, important_changes=important)

            # 自动打标：按出租方/承租方角色追加 enterprise_category（幂等防重）。
            # 属辅助增强，异常不影响导入主结果。
            try:
                await self._tag_enterprise_role(
                    result_obj.lessor_enterprise_id, "厂中厂出租"
                )
                await self._tag_enterprise_role(
                    result_obj.tenant_enterprise_id, "厂中厂承租"
                )
            except Exception:  # noqa: BLE001
                pass
            return wr
        except Exception as e:  # noqa: BLE001
            return WriteResult("failed", None, tname, str(e))


# 模块级单例
enterprise_writer = EnterpriseWriter()
material_writer = MaterialWriter()
inspection_writer = InspectionWriter()
factory_in_factory_writer = FactoryInFactoryWriter()
