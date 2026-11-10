"""企业信息「自由聚合 + 正则规范化」服务层。

以 SQL 主体企业为索引，把 Excel 多 sheet 的原始信息聚合成「信息池」
（每家企业的原始字符串值列表），再用 SQL 字段 + 枚举值域做正则规范化，
得到 {字段: 值} 键值对。与 import_writer 的「列名精确映射」互补：

  - import_parser / import_writer：列名 → 字段 的精确映射（主表等标准表）
  - 本模块：不关心列名，先按企业聚合原始值，再靠 SQL schema 正则归一化
    （用于列名不规范、跨 sheet 打标签、信息分散的补全场景）

设计（用户 09-17 定）：
  Tool A  build_pool           Excel → 信息池（原始字符串值列表），纯提取
  Tool 中 normalize_pool       pool + SQL键/枚举 → 正则 → {字段: 值}
  Tool B  backfill（写库）      以 SQL 主体为准补空字段（见 mcp_tools_backfill）
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

from app.services.svc_Import.import_parser import (
    normalize_name,
    load_sheet,
    read_rows,
    DEFAULT_XLSX_PATH,
)
from app.services.svc_Import.import_writer import (
    enterprise_writer,
    _PHONE_RE,
    _BUILDING_NO_RE,
)

# ── 正则规范化规则：按字段类型组织 ──────────────────────────────

# 枚举字段 → 允许值域（与 field_specs.allowed 一致，此处冗余以便纯函数独立可用）
# 注意：剔除"其他"这种宽泛值，避免任何含"其他"二字的字符串误匹配
_ENUM_VALUES: dict[str, tuple[str, ...]] = {
    "enterprise_category": ("劳密", "厂中厂出租", "厂中厂承租", "厂中厂", "小场所"),
    "asset_attribute": ("韩资", "台资"),
    "enterprise_scale": ("规上", "规下"),
    "fire_level": ("重点单位",),
    "confined_space": ("地上", "地下"),
}

# 枚举字段 → 列名关键词（规范化时列名命中关键词才采信该枚举值）
_ENUM_COL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "enterprise_category": ("企业类型", "企业属性", "类型", "劳动密集", "劳密", "厂中厂"),
    "asset_attribute": ("资产", "企业属性", "属性"),
    "enterprise_scale": ("规模", "规上", "规下"),
    "fire_level": ("消防", "重点单位", "消防等级"),
    "confined_space": ("有限空间", "地上", "地下", "空间"),
}

# text[] 数组枚举字段：normalize_pool 收集所有匹配值去重保序为 list，其余枚举字段取首个匹配
_ARRAY_ENUM_FIELDS: set[str] = {"enterprise_category", "confined_space"}

# 风险 bool 字段：信息池中出现关键词即 True
_BOOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "has_hazardous_chemicals": ("危化品", "危险化学品", "危险品"),
    "involves_dust_explosion": ("粉尘", "涉粉"),
    "has_natural_gas": ("天然气", "燃气"),
    "is_injection_molding": ("注塑",),
    "has_dormitory": ("宿舍", "住宿"),
    "has_hazardous_waste": ("危废", "危险废物"),
    "is_new_energy_welding": ("焊机", "电焊", "焊接"),
    "has_large_storage_tank": ("储罐", "储槽"),
}

# 行业关键词 → industry（复用 import_writer 的规则思路，独立维护一份精简版）
_INDUSTRY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("物流", "物流"), ("餐饮", "餐饮"), ("咖啡", "餐饮"),
    ("建材", "建材"), ("石材", "建材"), ("汽修", "汽车服务"),
    ("汽车服务", "汽车服务"), ("汽车城", "汽车及零配件销售店"),
    ("叉车", "设备租赁"), ("商贸", "商贸"), ("贸易", "商贸"),
    ("食品", "食品"), ("机械", "机械"), ("化工", "化工"),
    ("纺织", "纺织"), ("电子", "电子"), ("金属", "金属"),
    ("塑业", "塑业"), ("包装", "包装"),
)

# 地址关键词（判断字符串是地址）
_ADDRESS_KEYWORDS = ("路", "街", "号", "开发区", "园区", "大道", "大道", "路", "巷")

# 纯数字（整数，去除常见非人数数字后用于 employee_count）
_INT_RE = re.compile(r"^\d{1,6}$")
# 信用代码（18 位，9 开头）
_CODE_RE = re.compile(r"^[0-9A-Z]{18}$")


def build_pool(
    file_path: str = DEFAULT_XLSX_PATH,
    sheets: Optional[list[str]] = None,
    skip_sheets: tuple[str, ...] = ("WpsReserved_CellImgList",),
) -> dict:
    """Tool A 核心：遍历 Excel，以企业名称（归一化）为 key 聚合信息池。

    信息池结构：
        {
            "<归一化名称>": {
                "name": "<原始名称>",
                "code": "<信用代码，可能为空>",
                "raw_values": ["<原始字符串值>", ...],   # 去重、非空（供 agent 快速浏览）
                "raw_pairs": [{"col": "<列名>", "val": "<值>"}, ...],  # 保留列名+值
                "sources": ["<sheet名>", ...],          # 该企业出现在哪些 sheet
            }
        }

    保留「列名 + 值」原始对（raw_pairs），不硬编码列名→字段映射；
    规范化时用列名关键词 + 值正则联合判断，比纯值列表更准确。
    名称近似（归一化后相等）合并到同一 key。
    """
    pool: dict[str, dict] = {}
    sheet_names = sheets or []
    if not sheet_names:
        from app.services.svc_Import.import_parser import list_sheets
        sheet_names = list_sheets(file_path)

    for sheet in sheet_names:
        if sheet in skip_sheets:
            continue
        try:
            wb, ws = load_sheet(file_path, sheet)
        except Exception:
            continue
        # 定位表头行（宽松：第一个非空单元格数 >= 2 的行）
        header_row = _locate_header(ws)
        if header_row is None:
            continue
        try:
            rows = read_rows(ws, header_row=header_row)
        except Exception:
            continue

        for row in rows:
            name_val = _extract_name(row)
            if not name_val:
                continue
            key = normalize_name(name_val)
            if not key:
                continue
            # 厂房号剥离，便于匹配（保留原始名，key 用剥离后）
            stripped = _BUILDING_NO_RE.match(str(name_val).strip())
            key = normalize_name(stripped.group(1).strip()) if stripped else key

            entry = pool.setdefault(key, {
                "name": name_val,
                "code": "",
                "raw_values": [],
                "raw_pairs": [],
                "sources": [],
            })
            if sheet not in entry["sources"]:
                entry["sources"].append(sheet)

            # 收集该行所有非空「列名:值」对；顺带识别 code
            for col, v in row.items():
                if v is None:
                    continue
                s = str(v).strip()
                if not s:
                    continue
                col_name = str(col).strip() if col is not None else ""
                # code 识别
                if _CODE_RE.match(s.upper()) and s.upper().startswith("9"):
                    if not entry["code"]:
                        entry["code"] = s.upper()
                    continue  # code 不进 raw_values / raw_pairs
                # 去重后进 raw_values（纯值）
                if s not in entry["raw_values"]:
                    entry["raw_values"].append(s)
                # raw_pairs 保留列名 + 值（同列名+值才去重）
                pair = {"col": col_name, "val": s}
                if pair not in entry["raw_pairs"]:
                    entry["raw_pairs"].append(pair)

    return pool


def _locate_header(ws) -> Optional[int]:
    """定位表头行：第一个「非空单元格数 >= 2」且含名称类关键词的行。"""
    for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
        nonempty = [v for v in row if v is not None and str(v).strip()]
        if len(nonempty) >= 2:
            return i
    return None


def _extract_name(row: dict) -> Optional[str]:
    """从一行里找出企业名称列的值（优先匹配含"名称"的键）。"""
    for k, v in row.items():
        if v and ("名称" in str(k) or "单位" in str(k) or "企业" in str(k)):
            return str(v).strip()
    # 兜底：第一个较长的字符串值当作名称
    for v in row.values():
        if v and len(str(v).strip()) >= 4 and "公司" in str(v):
            return str(v).strip()
    return None


def normalize_pool(
    pool: dict,
    enum_values: Optional[dict[str, tuple[str, ...]]] = None,
) -> dict:
    """Tool 中核心：对信息池做正则规范化，得到 {key: {name, code, fields}}。

    输入 pool（build_pool 产物）+ 枚举值域（默认取 _ENUM_VALUES）。
    规范化规则（b：正则，不写库，无副作用）：
      1. 枚举字段：raw_values 中精确命中 allowed 值
      2. 风险 bool：raw_values 中出现关键词 → True
      3. industry：raw_values 命中行业关键词
      4. 手机号：正则提取到电话（存 fields["联系电话"]，人名靠 import_writer 兜底）
      5. 纯数字 → employee_count（取最大值，避开序号/年份等噪声）
      6. 地址：含地址关键词的最长字符串
    正则拿不准的字段留空，交由 agent（a）兜底检查。
    """
    enum_values = enum_values or _ENUM_VALUES
    result: dict = {}
    for key, entry in pool.items():
        raw = entry.get("raw_values", [])
        pairs = entry.get("raw_pairs", [])
        joined = " ".join(raw)
        fields: dict[str, Any] = {}

        # 1. 枚举：列名含关键词 且 值精确命中 allowed（比纯值匹配更准）
        # 旧值兼容：'厂中厂'（已拆分为出租/承租两值）→ 默认归 '厂中厂承租'
        # text[] 数组字段（enterprise_category）：收集所有匹配值去重保序为 list
        _LEGACY_ENUM_MAP = {"厂中厂": "厂中厂承租"}
        for field, allowed in enum_values.items():
            col_kw = _ENUM_COL_KEYWORDS.get(field, ())
            is_array = field in _ARRAY_ENUM_FIELDS
            matched: list = []
            for a in allowed:
                for p in pairs:
                    col, val = p["col"], p["val"]
                    # 列名命中关键词（或列名为空则退化为纯值精确相等）
                    col_hit = any(kw in col for kw in col_kw) if col_kw else True
                    if col_hit and (val == a or a in val):
                        v = _LEGACY_ENUM_MAP.get(a, a)
                        if is_array:
                            if v not in matched:
                                matched.append(v)
                        else:
                            fields[field] = v
                        break  # 内层 pairs break：该 a 已匹配
                if not is_array and field in fields:
                    break  # 标量字段：首个匹配即足够，不再查后续 a
            if is_array and matched:
                fields[field] = matched

        # 2. 风险 bool
        for field, kws in _BOOL_KEYWORDS.items():
            if any(kw in joined for kw in kws):
                fields[field] = True

        # 3. 行业
        for kw, ind in _INDUSTRY_KEYWORDS:
            if kw in joined:
                fields["industry"] = ind
                break

        # 4. 手机号
        phones = _PHONE_RE.findall(joined)
        if phones:
            fields["联系电话"] = phones[0]

        # 5. employee_count：列名含人数关键词 且 值是整数
        emp_candidates = []
        for p in pairs:
            col, val = p["col"], p["val"]
            if not any(kw in col for kw in ("人数", "职工", "从业", "员工")):
                continue
            if _INT_RE.match(val) and not _PHONE_RE.fullmatch(val):
                n = int(val)
                if 5 < n < 100000 and not (1900 <= n <= 2100):
                    emp_candidates.append(n)
        if emp_candidates:
            fields["employee_count"] = max(emp_candidates)

        # 6. 地址：列名含地址关键词 或 值含地址关键词的最长字符串
        addr_candidates = []
        for p in pairs:
            col, val = p["col"], p["val"]
            if any(kw in col for kw in ("地址", "住址", "所在")):
                if val:
                    addr_candidates.append(val)
        if not addr_candidates:
            addr_candidates = [
                v for v in raw
                if len(v) >= 6 and any(kw in v for kw in _ADDRESS_KEYWORDS)
            ]
        if addr_candidates:
            fields["address"] = max(addr_candidates, key=len)

        result[key] = {
            "name": entry["name"],
            "code": entry["code"],
            "fields": fields,
            "sources": entry["sources"],
        }
    return result
