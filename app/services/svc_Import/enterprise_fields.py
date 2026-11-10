"""企业补全字段的权威 schema 与校验（无 LLM）。

本模块是"对话式企业字段补全"链路的服务端安全闸门：
  1. get_field_schema()  —— 把允许补全的字段目录、类型、枚举值域、字段说明
                            以结构化 JSON 暴露给 Agent（Agent 据此组装字段，禁止臆造字段名）。
  2. validate_fields()   —— Agent 提交字段后在服务端做强校验：白名单 / 枚举值域 /
                            类型 / 去重，过滤一切幻觉值。

字段定义不硬编码：运行时从 import_writer.enterprise_writer.field_specs +
Enterprise 模型列类型动态推导，字段或枚举变化时自动跟随。

历史：本模块由 agent_extract.py（RAG → 拼 prompt → 调 deepseek 提取）重构而来。
"阅读理解原文 → 生成结构化 JSON"这一步已上移给 Hermes（Agent 本身即 LLM），
容器内不再发起任何 LLM 调用；检索原文用 MCP 工具 rag_docs_search，
提交字段用 MCP 工具 sql_enterprise_fields_commit。
"""
from __future__ import annotations

from app.services.svc_Import.import_writer import enterprise_writer
from app.db.models import Enterprise

# ── schema 元信息：从 SQL field_specs + 模型列类型动态生成 ──────────
_STRING_FIELDS: list[str] = []
_ENUM_FIELDS: dict[str, tuple] = {}        # 单值枚举 field -> allowed
_ARRAY_ENUM_FIELDS: dict[str, tuple] = {}  # 多值枚举（text[]）field -> allowed
_BOOL_FIELDS: set[str] = set()
_INT_FIELDS: set[str] = set()

for _f, _spec in enterprise_writer.field_specs.items():
    if _spec.is_identity:
        continue
    if _spec.data_type == "string_array" and _spec.allowed:
        _ARRAY_ENUM_FIELDS[_f] = _spec.allowed
    elif _spec.allowed:
        _ENUM_FIELDS[_f] = _spec.allowed
    elif _spec.data_type == "value":
        # value 类型可能是 bool 或 int，用模型列类型区分
        col_type = str(Enterprise.__table__.columns[_f].type)
        if col_type == "BOOLEAN":
            _BOOL_FIELDS.add(_f)
        elif col_type == "INTEGER":
            _INT_FIELDS.add(_f)
    elif _spec.data_type == "string":
        _STRING_FIELDS.append(_f)
    # array（jsonb tags 等）不纳入补全

# 字段中文语义说明（返回给 Agent，帮助其理解每个字段含义）
_FIELD_DESC = {
    "industry": "行业大类（机械/纺织/化工/汽车/电子/建材/物流/食品/金属/塑业/包装等）",
    "address": "企业地址",
    "legal_person": "法定代表人姓名",
    "person_in_charge": "企业负责人/联系人姓名（可含电话，如\"张三 138xxx\"）",
    "safety_officer": "安全负责人/安全员姓名（可含电话）",
    "asset_attribute": "资产属性（香港独资→台资、中外合资→其他、外资→其他、韩资→韩资）",
    "enterprise_category": "企业类型标签（可多个，数组提交）",
    "enterprise_scale": "企业规模",
    "fire_level": "消防等级",
    "confined_space": "有限空间位置多标签（地上/地下，可多选数组提交）",
    "confined_space_count": "有限空间数量（数字字符串，如 2/30）",
    "confined_space_type": "有限空间类型详细描述（如 地上、地下及封闭空间）",
    "confined_space_workers": "有限空间作业人数（数字字符串）",
    "confined_space_hazards": "有限空间危险因素（如 缺氧、中毒和窒息）",
    "confined_space_op_subject": "有限空间作业主体（自行/发包等）",
    "confined_space_op_cycle": "有限空间作业周期",
    "confined_space_fixed_workers": "有限空间作业人员是否固定（是/否）",
    "hazchem_names": "危化品名称（多种以、分隔）",
    "hazchem_op_subject": "危化品作业主体（自行/发包/自行和发包）",
    "hazchem_major_hazard": "是否重大危险源（构成重大危险源/不构成重大危险源）",
    "hazchem_state": "危化品状态（液态/固态/气态，可多值以、分隔）",
    "hazchem_op_cycle": "危化品作业周期（月/年）",
    "hazchem_usage": "危化品用途（生产/实验/其他，可多值）",
    "hazchem_annual_usage": "年度使用量（如 180t，可多值以、分隔）",
    "hazchem_storage": "存储方式（储罐/危化品库/中间库/防爆柜，可多值）",
    "hazchem_reserve": "危化品储量（如 0.7t，可多值以、分隔）",
    "hazchem_fixed_workers": "危化品作业人员是否固定（是/否）",
    "employee_count": "职工人数（单位：人）",
    "annual_output_value": "年产值或年营业收入（单位：万元）",
    "has_natural_gas": "是否使用天然气",
    "is_injection_molding": "是否注塑企业",
    "has_dormitory": "是否有宿舍",
    "has_hazardous_chemicals": "是否涉及危化品",
    "has_hazardous_waste": "是否涉及危废",
    "involves_dust_explosion": "是否涉及粉尘爆炸",
    "is_new_energy_welding": "是否新能源焊机",
    "has_large_storage_tank": "是否有大型储罐",
}

# 提交给 Agent 的全局规则说明
_RULES = [
    "只提交能从资料原文中明确推断的字段，推断不出的不要提交（禁止猜测）",
    "字段名必须来自本目录，白名单外的字段会被服务端直接丢弃",
    "enum / string_array 字段的值必须逐字取自 allowed 列表，不能自创或改写",
    "string_array 字段以字符串数组提交，如 [\"厂中厂出租\", \"劳密\"]",
    "int 字段提交非负整数；bool 字段提交 true/false；string 字段提交非空字符串",
    "sql_enterprise_fields_commit 只补目标企业当前为空的字段，不会覆盖已有值",
]


def get_field_schema() -> dict:
    """返回结构化字段目录（供 MCP 工具 enterprise_field_schema_get 直接序列化）。

    结构：
      {
        "fields": {
          字段名: {"type": string|enum|string_array|int|bool,
                  "allowed": [...仅枚举类],
                  "description": 中文说明}
        },
        "rules": [全局约束, ...]
      }
    """
    fields: dict[str, dict] = {}

    def _add(name: str, ftype: str, allowed: tuple | None = None) -> None:
        item: dict = {"type": ftype, "description": _FIELD_DESC.get(name, "")}
        if allowed:
            item["allowed"] = list(allowed)
        fields[name] = item

    for f in _STRING_FIELDS:
        _add(f, "string")
    for f, allowed in _ENUM_FIELDS.items():
        _add(f, "enum", allowed)
    for f, allowed in _ARRAY_ENUM_FIELDS.items():
        _add(f, "string_array", allowed)
    for f in sorted(_INT_FIELDS):
        _add(f, "int")
    for f in sorted(_BOOL_FIELDS):
        _add(f, "bool")

    return {"fields": fields, "rules": _RULES}


def field_type_of(name: str) -> str | None:
    """返回字段的校验类型标签（enum/string_array/int/bool/string），非白名单字段返回 None。"""
    if name in _ENUM_FIELDS:
        return "enum"
    if name in _ARRAY_ENUM_FIELDS:
        return "string_array"
    if name in _INT_FIELDS:
        return "int"
    if name in _BOOL_FIELDS:
        return "bool"
    if name in _STRING_FIELDS:
        return "string"
    return None


def validate_fields(fields: dict) -> dict:
    """校验 Agent 提交的字段，返回干净、合法的 {字段: 值}。

    规则：
      1. 白名单外（不在 field_specs 非 identity 字段）的字段丢弃
      2. 单值枚举：值必须在 allowed 内，否则丢弃（防越界自造）
      3. 多值枚举（text[]）：接受 list 或单字符串，逐项过滤 allowed 后去重保序
      4. int 字段：必须是非负整数，否则丢弃
      5. bool 字段：转成 True/False，无法判定则丢弃
      6. string 字段：去空白，空则丢弃
    """
    clean: dict = {}
    for k, v in fields.items():
        if k in _ENUM_FIELDS:
            s = str(v).strip() if v is not None else ""
            if s in _ENUM_FIELDS[k]:
                clean[k] = s
        elif k in _ARRAY_ENUM_FIELDS:
            if isinstance(v, (list, tuple)):
                candidates = v
            elif v is not None:
                candidates = [v]
            else:
                continue
            seen: set = set()
            vals: list[str] = []
            for item in candidates:
                s = str(item).strip() if item is not None else ""
                if s in _ARRAY_ENUM_FIELDS[k] and s not in seen:
                    seen.add(s)
                    vals.append(s)
            if vals:
                clean[k] = vals
        elif k in _INT_FIELDS:
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n >= 0:
                clean[k] = n
        elif k in _BOOL_FIELDS:
            if isinstance(v, bool):
                clean[k] = v
            elif isinstance(v, str):
                low = v.strip().lower()
                if low in ("true", "是", "1", "yes"):
                    clean[k] = True
                elif low in ("false", "否", "0", "no"):
                    clean[k] = False
        elif k in _STRING_FIELDS:
            s = str(v).strip() if v is not None else ""
            if s:
                clean[k] = s
        # 其它字段（identity / 未知）→ 丢弃
    return clean
