"""导入解析层（PARSER / L4）—— Excel 解析 + 按主键聚合，纯函数不碰数据库。

分层定位（用户 09-15 设计）：
  L4  本模块（解析 + 聚合）
      输入：sheet_name + col_map + field_specs + identity_fields
      输出：[(identity_dict, fields_dict), ...] —— 每条记录一个元组
      职责：读 Sheet → 定位表头 → 列映射 → 值规范化 → 按主键聚合多行
      不碰数据库，纯解析

  L3  import_writer.TableWriter.preview / commit
      接收 L4 产出的 (identity, fields) → resolve → 遍历 fields 调 L2 → write

聚合规则（同一记录多行场景，如危化品使用企业每种危化品一行）：
  标量: 最后一个非空
  bool: 任一为真
  extra: key 级合并，同 key 多值聚成数组

通用化设计（09-15）：
  normalize_row 从 field_specs 推导规范化器（不再硬编码企业字段）：
    data_type="value" → 先 parse_bool，再 parse_int
    data_type="string" + allowed 非空 → parse_enum(allowed)
    data_type="string" + allowed 空 → strip
    "extra:键名" → extra dict
  suggest_col_map / suggest_sheet_tags 接收 writer 的规则列表（不再硬编码）

列映射语法（col_map 值，即 Excel 列名 -> 目标）：
  "name" / "code" / "industry" ...   直落同名字段
  "extra:字段名"                     落 extra jsonb（如 "extra:街道"）
"""
from __future__ import annotations

import re
from typing import Any, Optional

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

# 容器内默认数据源（宿主机 data/uploads 挂载到 /app/data/uploads）。
# 注意：这是「企业监管」业务专用的默认 Excel（各类企业情况汇总），
# 仅作为各导入工具 file_path 参数的缺省值；解析函数本身不绑定该文件，
# 导入其他工作簿时显式传 file_path 即可。
DEFAULT_XLSX_PATH = "/app/data/uploads/各类企业情况汇总（2026）.xlsx"


class ParserError(ValueError):
    """Excel 解析不合规（文件/Sheet 不存在、找不到表头等）。"""


# ── 基础读取 ──────────────────────────────────────────────────
def clean_cell(v: Any) -> Any:
    """单元格清理：全角空格/换行/首尾空白；纯空返回 None。"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.replace("　", " ").replace("\n", " ").strip()
        return v or None
    return v


# 名称归一化用的全角→半角对照表（只做高置信等价，不删"有限公司"等前后缀）
_NAME_TRANS = str.maketrans({
    "（": "(", "）": ")", "【": "[", "】": "]",
    "，": ",", "：": ":", "　": "",
})


def normalize_name(v: Any) -> str:
    """实体名称归一化：全角标点转半角 + 去全部空白/换行。

    用于跨 sheet 名称匹配的高置信兜底（仅当归一化后**完全相等**才自动命中，
    不做包含/简称推断——低置信候选在 workbook_plan 中交人工确认）。
    """
    if v is None:
        return ""
    return re.sub(r"\s+", "", str(v).translate(_NAME_TRANS))


def list_sheets(path: str = DEFAULT_XLSX_PATH) -> list[str]:
    """列出工作簿全部 Sheet 名（探测用，只读名称不读内容）。"""
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except FileNotFoundError:
        raise ParserError(f"文件不存在：{path}")
    names = wb.sheetnames
    wb.close()
    return names


def load_sheet(path: str, sheet_name: str):
    """打开工作簿返回指定 worksheet（data_only 取公式计算值）。"""
    try:
        wb = load_workbook(path, read_only=False, data_only=True)
    except FileNotFoundError:
        raise ParserError(f"文件不存在：{path}")
    if sheet_name not in wb.sheetnames:
        raise ParserError(
            f"Sheet「{sheet_name}」不存在，可用：{'、'.join(wb.sheetnames)}"
        )
    return wb, wb[sheet_name]


def locate_header(ws, scan_rows: int = 8, min_cols: int = 3) -> int:
    """定位真实表头行号（1-based）。"""
    for i, row in enumerate(
        ws.iter_rows(min_row=1, max_row=min(scan_rows, ws.max_row)), start=1
    ):
        filled = sum(1 for c in row if clean_cell(c.value) is not None)
        if filled >= min_cols:
            return i
    raise ParserError(
        f"Sheet「{ws.title}」前 {scan_rows} 行未找到表头（非空格数 >= {min_cols}）"
    )


def read_rows(ws, header_row: Optional[int] = None) -> list[dict]:
    """表头行 + 数据行 -> [{列名: 值}]；列名为空用「列N」兜底，整行全空跳过。"""
    if header_row is None:
        header_row = locate_header(ws)
    rows_iter = ws.iter_rows(min_row=header_row, max_row=ws.max_row)
    header = []
    for c in next(rows_iter, []):
        v = clean_cell(c.value)
        # 列名压缩内部空白：兼容"企业 名称""危化品 名称"等排版松散的表头，
        # 使列名关键词规则稳定命中（inspect/parse/col_map 看到的列名保持一致）
        header.append(re.sub(r"\s+", "", str(v)) if v is not None
                      else f"列{get_column_letter(c.column)}")

    out = []
    for row in rows_iter:
        record = {}
        for c, col_name in zip(row, header):
            record[col_name] = clean_cell(c.value)
        if any(v is not None for v in record.values()):
            out.append(record)
    return out


def read_rows_by_column(ws, skip_rows: int = 0) -> list[dict]:
    """无表头 Sheet 按列字母读取 -> [{列字母: 值}]（跳过前 skip_rows 行）。

    用于首行就是数据、没有列名的 Sheet（如"2026年9月隐患"）：
    调用方自行提供 {列字母: 目标字段} 的位置映射。
    整行全空跳过。
    """
    out = []
    for row in ws.iter_rows(min_row=1 + skip_rows, max_row=ws.max_row):
        record = {}
        for c in row:
            record[get_column_letter(c.column)] = clean_cell(c.value)
        if any(v is not None for v in record.values()):
            out.append(record)
    return out


# ── 值规范化（通用函数）──────────────────────────────────────
_TRUE_WORDS = {"是", "有", "涉及", "y", "yes", "true", "1", "√", "✓"}
_FALSE_WORDS = {"否", "无", "不涉及", "n", "no", "false", "0", "×", "-"}


def parse_bool(v: Any) -> Optional[bool]:
    """是/否类脏值 -> bool；识别不了返回 None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _TRUE_WORDS:
        return True
    if s in _FALSE_WORDS:
        return False
    return None


def parse_int(v: Any) -> Optional[int]:
    """'120人' / '120' / 120 -> 120；识别不了返回 None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else None


def parse_enum(v: Any, allowed: tuple[str, ...]) -> Optional[str]:
    """枚举值容错：包含任一允许词即命中（如"规上企业"->"规上"）。"""
    if v is None:
        return None
    s = str(v).strip()
    for a in allowed:
        if a in s:
            return a
    return None


def _is_blank(v: Any) -> bool:
    """None / 空串 / 空数组视为空值。"""
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, tuple, dict)) and len(v) == 0:
        return True
    return False


# ── 隐患内容拆分（一行多隐患 → 多行一隐患）────────────────────
# 支持的序号写法：
#   1、xxx；2、yyy        中文顿号（最常见，分隔符可为 ；;。换行）
#   1. xxx 2. yyy        英文/中文点号
#   ①xxx ②yyy           圆圈数字
#   （1）xxx（2）yyy      括号数字（半角/全角）
_HAZARD_MARKER_RE = re.compile(
    r'\d{1,2}\s*[、.．]|[①-⑳]|（\d{1,2}）|\(\d{1,2}\)'
)
_HAZARD_SEP_CHARS = ('；', ';', '。', '\n')


def split_hazards(content: Any) -> list[str]:
    """把"1、xxx；2、yyy；3、zzz"拆成 ["xxx", "yyy", "zzz"]。

    只切分"位于串首或紧跟分隔符之后"的序号，避免误切隐患正文里的
    数字（如"100平""3人""1.2米"）。无序号的整段文本按单条隐患返回。
    """
    if content is None:
        return []
    s = str(content).strip()
    if not s:
        return []

    # 收集合法序号位置：序号在串首、紧跟分隔符（；;。换行），
    # 或紧跟空白（如"1. xxx 2. yyy"、"①xxx ②yyy"）。
    # "1.2米"不会误切：小数点前是数字，既非分隔符也非空白。
    positions: list[tuple[int, int]] = []
    for m in _HAZARD_MARKER_RE.finditer(s):
        before = s[:m.start()]
        trimmed = before.rstrip()
        if not trimmed or trimmed[-1] in _HAZARD_SEP_CHARS:
            positions.append((m.start(), m.end()))
        elif before and before[-1] in (' ', '\t'):
            positions.append((m.start(), m.end()))

    if not positions:
        return [s]

    parts: list[str] = []
    # 第一个序号之前的残余文本（一般为空）
    if positions[0][0] > 0 and s[:positions[0][0]].strip():
        parts.append(s[:positions[0][0]].strip())
    for i, (_st, en) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(s)
        piece = s[en:end].strip(' \t；;。\n\r')
        if piece:
            parts.append(piece)
    return parts


# ── 行映射（通用：从 field_specs 推导规范化器）─────────────────
def normalize_row(
    row: dict,
    col_map: dict[str, str],
    field_specs: dict,
    *,
    strict: bool = False,
) -> tuple[dict, list[str]]:
    """原始行 dict -> 骨架 dict（字段对齐 writer 的 field_specs）。

    通用化设计：规范化器从 field_specs 推导，不硬编码具体字段：
      data_type="value" → 先 parse_bool，再 parse_int
      data_type="string" + allowed 非空 → parse_enum(allowed)
      data_type="string" + allowed 空 → strip
      "extra:键名" → extra dict
      未在 field_specs 中声明的目标字段 → 当文本 strip

    Args:
        row: 原始行 dict {Excel列名: 值}
        col_map: 列映射 {Excel列名: 目标字段 或 "extra:键名"}
        field_specs: writer 的 field_specs 字典 {字段名: FieldSpec}
        strict: True 时遇不可解析值抛错，默认收集错误

    Returns:
        (skeleton, errors)
    """
    skeleton: dict = {}
    extra: dict = {}
    errors: list[str] = []

    for col_name, target in col_map.items():
        raw = row.get(col_name)
        if raw is None:
            continue

        # _merge_phone:xxx → 把电话值拼到 xxx 姓名字段（如 person_in_charge）
        if target.startswith("_merge_phone:"):
            phone_target = target[len("_merge_phone:"):].strip()
            phone_val = str(raw).strip()
            if phone_val and phone_target:
                existing = skeleton.get(phone_target, "")
                if existing and phone_val not in existing:
                    # 已有名字，追加电话
                    skeleton[phone_target] = f"{existing} {phone_val}".strip()
                elif not existing:
                    # 还没名字，先存电话（等后面姓名字段进来再拼）
                    skeleton[phone_target] = phone_val
            continue

        # extra:键名 → extra dict
        if target.startswith("extra:"):
            key = target[len("extra:"):].strip()
            if key and raw is not None:
                extra[key] = str(raw)
            continue

        # 从 field_specs 推导规范化器
        spec = field_specs.get(target)
        if spec is not None and not spec.is_identity:
            data_type = spec.data_type
            allowed = spec.allowed

            if data_type == "value":
                # 先试 bool，再试 int
                v = parse_bool(raw)
                if v is None:
                    v = parse_int(raw)
                if v is None and raw is not None:
                    errors.append(f"「{col_name}」值'{raw}'无法解析为数值或是/否")
                    if strict:
                        raise ParserError(errors[-1])
                    continue
                if v is not None:
                    skeleton[target] = v
                continue

            if data_type == "string" and allowed:
                v = parse_enum(raw, allowed)
                if v is None and raw is not None:
                    errors.append(f"「{col_name}」值'{raw}'不在 {'/'.join(allowed)}")
                    if strict:
                        raise ParserError(errors[-1])
                    continue
                if v is not None:
                    skeleton[target] = v
                continue

        # 普通文本字段（含身份键、无 allowed 的 string）
        if raw is not None:
            new_val = str(raw).strip()
            existing = skeleton.get(target, "")
            if existing and new_val != existing:
                import re as _re
                # existing 是纯电话，new_val 是名字 → 拼接（电话先到，名字后到）
                if _re.search(r'(1[3-9]\d{9}|0\d{2,3}-?\d{7,8})', existing) and not _re.search(r'(1[3-9]\d{9}|0\d{2,3}-?\d{7,8})', new_val):
                    # 如果 existing 已经以 new_val 开头（名字已在前面，只是多了电话），跳过
                    if not existing.startswith(new_val):
                        skeleton[target] = f"{new_val} {existing}".strip()
                    # 否则保持 existing（已经有名字+电话了）
                else:
                    # 普通覆盖（不同值）
                    skeleton[target] = new_val
            # existing == new_val → 跳过（避免同名重复）
            elif not existing:
                skeleton[target] = new_val

    if extra:
        skeleton["extra"] = extra
    return skeleton, errors


# ── 建议映射（通用：从 writer 规则推导）────────────────────────
def suggest_col_map(headers: list[str], col_map_rules: list = ()) -> dict[str, str]:
    """按列名关键词给出建议 col_map（决策 #11，仅预填、人工可改）。

    通用化：规则列表由 writer 子类提供，不再硬编码。
    """
    col_map: dict[str, str] = {}
    for h in headers:
        for kw, field in col_map_rules:
            if kw in h and h not in col_map:
                col_map[h] = field
                break
    return col_map


def suggest_sheet_tags(sheet_name: str, sheet_tag_rules: list = ()) -> Optional[dict]:
    """按 Sheet 名给出建议标签（决策 #11）。

    通用化：规则列表由 writer 子类提供，不再硬编码。
    """
    for kw, tag in sheet_tag_rules:
        if kw in sheet_name:
            return tag
    return None


def inspect_sheet(
    path: str,
    sheet_name: str,
    field_specs: Optional[dict] = None,
    col_map_rules: list = (),
    sheet_tag_rules: list = (),
) -> dict:
    """探测 Sheet 结构：表头行、列名、样本行、建议映射/标签（TOOL 层用）。

    通用化：field_specs + 规则列表由 writer 提供。
    """
    wb, ws = load_sheet(path, sheet_name)
    try:
        header_row = locate_header(ws)
        rows = read_rows(ws, header_row)
        headers = list(rows[0].keys()) if rows else []
        sample = rows[:3]
        return {
            "sheet": sheet_name,
            "dims": f"{ws.max_row} 行 × {ws.max_column} 列",
            "header_row": header_row,
            "headers": headers,
            "sample_rows": sample,
            "data_row_count": len(rows),
            "suggested_col_map": suggest_col_map(headers, col_map_rules) if col_map_rules else {},
            "suggested_tag": suggest_sheet_tags(sheet_name, sheet_tag_rules) if sheet_tag_rules else None,
        }
    finally:
        wb.close()


# ── L4 核心：按主键聚合 ─────────────────────────────────────────
def _merge_skeletons(acc: dict, inc: dict) -> dict:
    """聚合两行（同一记录多行场景）。

    规则：
      标量(str/int): 最后一个非空覆盖
      bool:          任一为真
      dict(extra):   key 级合并，同 key 多值聚成数组
    """
    result = dict(acc)
    for k, v in inc.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if k not in result or _is_blank(result[k]):
            result[k] = v
            continue
        if isinstance(v, bool):
            result[k] = result[k] or v
        elif isinstance(v, dict):
            # extra: key 级合并，同 key 多值聚成数组
            for ek, ev in v.items():
                if ev is None or (isinstance(ev, str) and not ev.strip()):
                    continue
                if ek not in result[k] or _is_blank(result[k][ek]):
                    result[k][ek] = ev
                elif isinstance(result[k][ek], list):
                    if ev not in result[k][ek]:
                        result[k][ek].append(ev)
                elif result[k][ek] != ev:
                    result[k][ek] = [result[k][ek], ev]
        else:
            # 标量: 最后一个非空覆盖
            result[k] = v
    return result


def aggregate_by_key(
    skeletons: list[dict],
    identity_fields: tuple[str, ...],
    sheet_tag: Optional[dict] = None,
) -> list[tuple[dict, dict]]:
    """按主键聚合多行 -> [(identity, fields), ...]

    L4 核心函数：把 normalize_row 产出的骨架 dict 列表，按身份键分组聚合。

    Args:
        skeletons: normalize_row 产出的骨架 dict 列表
        identity_fields: 身份键字段名（如 ("code", "name")）
        sheet_tag: Sheet 名暗示的标签 {"field": ..., "value": ...}（来自 suggest_sheet_tags）
                   value=None 表示值从行内取（如有限空间的地上/地下列）

    Returns:
        [(identity_dict, fields_dict), ...] —— 每条记录一个元组
        identity_dict 只含身份键
        fields_dict 只含数据字段
    """
    # Step 1: 按身份键分组 + 聚合
    groups: dict[str, dict] = {}
    key_order: list[str] = []

    last_key = ""
    for sk in skeletons:
        # 组合【全部】非空身份键生成分组键。
        # 不能只取第一个：复合主键（如隐患表的 batch+enterprise_name+hazard_index）
        # 若只取 batch，同一次批次的所有企业/所有隐患都会被错误并成一组。
        parts = [
            f"{f}:{str(sk.get(f)).strip()}"
            for f in identity_fields
            if sk.get(f) is not None and str(sk.get(f)).strip()
        ]
        group_key = "|".join(parts)
        if not group_key:
            # Excel 竖向合并单元格展开后身份键全空 → 明细行归属上一企业
            if last_key:
                groups[last_key] = _merge_skeletons(groups[last_key], sk)
            continue

        last_key = group_key
        if group_key not in groups:
            groups[group_key] = dict(sk)
            key_order.append(group_key)
        else:
            groups[group_key] = _merge_skeletons(groups[group_key], sk)

    # Step 2: 拆分 identity / fields + 加 sheet_tag
    result: list[tuple[dict, dict]] = []
    for gk in key_order:
        merged = groups[gk]
        identity: dict = {}
        fields: dict = {}
        for k, v in merged.items():
            if k in identity_fields:
                identity[k] = v
            else:
                fields[k] = v

        # 加 Sheet 名暗示的标签
        if sheet_tag:
            tag_field = sheet_tag["field"]
            tag_value = sheet_tag["value"]
            if tag_value is not None and tag_field not in fields:
                fields[tag_field] = tag_value
            # value=None 表示值从行内取（已在 fields 中）

        result.append((identity, fields))

    return result


# ── 全工作簿导入计划（多 sheet 编排）──────────────────────────
# 本模块是**通用 Excel 解析层**：不内置任何业务表/业务工作簿的词表。
# sheet 名归类、实体列表头识别、关系表/跳过表判定等启发式词表，
# 一律由调用方（如企业表 EnterpriseWriter.workbook_hints）经 workbook_plan(
# hints=...) 参数注入；不传 hints 时退化为只依赖 col_map_rules /
# sheet_tag_rules 的纯通用模式（material/inspection 等表即如此）。
#
# 唯一保留的默认中文线索：括号内"现名/别名/更名"改名模式——
# 属于通用中文实体名称清洗知识（非某个业务表专有），hints 可覆盖。
DEFAULT_ALIAS_PATTERN = r"(?:现名|别名|更名)[：:]?\s*([^()（）]+)"


def _locate_plan_header(ws, entity_keywords: tuple = ()) -> int:
    """计划专用表头定位：优先选含实体列表头关键词最多的行（兼容标题行、两行表头）。

    locate_header 默认取首个非空 ≥3 的行，遇到"标题占2格 + 表头在下一行"
    或两行复合表头会误判；这里在前 8 行内按调用方注入的实体列关键词
    打分选最佳行，全无时退回首个非空 ≥2 行。
    """
    best_row, best_score = None, 0
    first_qualified = None
    for i, row in enumerate(
        ws.iter_rows(min_row=1, max_row=min(8, ws.max_row)), start=1
    ):
        texts = [str(clean_cell(c.value)) for c in row
                 if clean_cell(c.value) is not None]
        if len(texts) >= 2 and first_qualified is None:
            first_qualified = i
        score = sum(1 for t in texts if any(k in t for k in entity_keywords))
        if score > best_score:
            best_row, best_score = i, score
    if best_row is not None:
        return best_row
    if first_qualified is not None:
        return first_qualified
    raise ParserError("前 8 行未找到表头")


def _entity_name_columns(headers: list[str], entity_keywords: tuple = ()) -> list[str]:
    """按调用方注入的关键词，从表头中挑出可能承载实体名称的列。"""
    return [h for h in headers if any(k in h for k in entity_keywords)]


def _extract_entity_names(rows: list[dict], name_cols: list[str]) -> list[str]:
    """从数据行中提取去重后的实体名称（保持出现顺序）。"""
    seen: set[str] = set()
    out: list[str] = []
    for r in rows:
        for nc in name_cols:
            v = r.get(nc)
            if v is None:
                continue
            s = str(v).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def match_candidates(
    incoming: str,
    db_names: list[str],
    alias_re: Optional[re.Pattern] = None,
) -> list[str]:
    """低置信候选（不自动写入，仅供人工确认）：

    1) 括号内别名/改名线索（alias_re）命中库内名称（精确或归一化）；
    2) 双向包含且长度差 ≤4、双方均 ≥6 字（避免短词误匹配）。
    """
    found: list[str] = []
    target_norm = normalize_name(incoming)
    if alias_re is not None:
        for m in alias_re.finditer(incoming):
            alias = m.group(1).strip()
            if not alias:
                continue
            alias_norm = normalize_name(alias)
            for n in db_names:
                if n == alias or (alias_norm and normalize_name(n) == alias_norm):
                    if n not in found:
                        found.append(n)
    if not found and len(target_norm) >= 6:
        for n in db_names:
            nn = normalize_name(n)
            if (
                len(nn) >= 6
                and abs(len(nn) - len(target_norm)) <= 4
                and (nn in target_norm or target_norm in nn)
            ):
                found.append(n)
                break
    return found[:3]


def workbook_plan(
    path: str,
    field_specs: Optional[dict] = None,
    col_map_rules: list = (),
    sheet_tag_rules: list = (),
    *,
    db_index: Optional[dict] = None,
    hints: Optional[dict] = None,
    max_unmapped_sample: int = 6,
    max_ambiguous: int = 10,
) -> list[dict]:
    """扫描工作簿全部 sheet，产出导入计划（纯函数，不碰数据库）。

    db_index 由 TOOL 层从库内灌入：{"exact": set(实体名), "names": list(实体名)}，
    用于预估每个 sheet 的名称匹配率；传 None 则跳过匹配预估。

    hints 为业务工作簿专有的启发式词表（由各表 writer 的 workbook_hints
    声明，本函数不内置任何业务词）。全部键可选，缺省即不启用该判定：
      entity_keywords        表头中识别实体名称列的关键词
      strong_entity_keywords 强实体列关键词（缺失时，sheet 名命中
                              relation_sheet_keywords 判为关系表）
      skip_sheet_keywords    sheet 名命中即跳过（非业务数据表）
      relation_sheet_keywords  租赁/从属等"双实体关系表"信号词
      tag_conflict_words     与 sheet 标签可能冲突的信号词（仅提示）
      alias_pattern          改名/别名正则字符串（默认通用中文线索）

    每个 sheet 的 recommendation（以下文案为通用描述；具体实体称谓、
    标签含义由调用方的 hints/规则决定）：
      tag_only  sheet 名即标签，解析出实体名称列即可打标
      mapped    有可按规则映射的数据列，走 parse_sheet + col_map
      review    含实体列但无规则命中（一对多明细/双实体关系/标签值需行内提取）
      skip      非业务数据表或表头无法识别
    """
    exact_names = (db_index or {}).get("exact") or set()
    db_names = (db_index or {}).get("names") or []
    norm_index = {normalize_name(n): n for n in db_names}
    # 业务启发式词表全部来自调用方注入，本层不提供任何业务默认值
    h = hints or {}
    entity_keywords = tuple(h.get("entity_keywords") or ())
    strong_entity_keywords = tuple(h.get("strong_entity_keywords") or ())
    skip_sheet_keywords = tuple(h.get("skip_sheet_keywords") or ())
    relation_sheet_keywords = tuple(h.get("relation_sheet_keywords") or ())
    tag_conflict_words = tuple(h.get("tag_conflict_words") or ())
    alias_re = re.compile(h.get("alias_pattern") or DEFAULT_ALIAS_PATTERN)
    plan: list[dict] = []

    for sheet in list_sheets(path):
        entry: dict[str, Any] = {"sheet": sheet}

        if any(k in sheet for k in skip_sheet_keywords):
            entry.update(recommendation="skip",
                         reason="命中 skip_sheet_keywords：非业务数据表（如账号/系统保留表）")
            plan.append(entry)
            continue

        try:
            wb, ws = load_sheet(path, sheet)
            try:
                header_row = _locate_plan_header(ws, entity_keywords)
                rows = read_rows(ws, header_row)
                dims = f"{ws.max_row} 行 × {ws.max_column} 列"
            finally:
                wb.close()
        except ParserError as e:
            entry.update(recommendation="review", data_row_count=0,
                         reason=f"表头定位失败：{e}")
            plan.append(entry)
            continue

        headers = list(rows[0].keys()) if rows else []
        name_cols = _entity_name_columns(headers, entity_keywords)
        strong_name_cols = [
            h_col for h_col in headers
            if any(k in h_col for k in strong_entity_keywords)
        ]
        is_relation_sheet = (
            not strong_name_cols
            and any(k in sheet for k in relation_sheet_keywords)
        )
        suggested_tag = (
            suggest_sheet_tags(sheet, sheet_tag_rules) if sheet_tag_rules else None
        )
        # 关系表不采信 sheet 名标签（如"新能源园区厂房出租表"会误命中焊机标签）
        if is_relation_sheet:
            suggested_tag = None
        col_map = suggest_col_map(headers, col_map_rules) if col_map_rules else {}
        # 身份键（name/code）不算"有效数据映射"
        useful = {h_col: t for h_col, t in col_map.items() if t not in ("name", "code")}
        unmapped = [h_col for h_col in headers if h_col not in col_map][:max_unmapped_sample]
        entity_names = _extract_entity_names(rows, name_cols)
        # 一对多信号：数据行数明显多于去重实体数（明细行/风险点表）
        one_to_many = (
            bool(entity_names)
            and len(rows) > len(entity_names) * 1.5
            and len(rows) - len(entity_names) >= 3
        )

        # sheet 名含标签规则词之外的其他强信号词 → 提示标签可能误判
        tag_conflict: list[str] = []
        if suggested_tag is not None and sheet_tag_rules:
            hit_kw = next((kw for kw, _ in sheet_tag_rules if kw in sheet), None)
            tag_conflict = [
                w for w in tag_conflict_words
                if w in sheet and w != hit_kw
            ]

        entry.update({
            "dims": dims,
            "header_row": header_row,
            "data_row_count": len(rows),
            "identity_columns": name_cols,
            "entity_count": len(entity_names),
            "suggested_tag": suggested_tag,
            "suggested_col_map": col_map,
            "unmapped_header_sample": unmapped,
        })
        if tag_conflict:
            entry["tag_conflict"] = tag_conflict
        if one_to_many:
            entry["one_to_many"] = {
                "rows": len(rows),
                "entities": len(entity_names),
                "hint": "疑似一对多明细表，直接导入会令标量被末行覆盖；打 bool 标签幂等安全",
            }

        # ── 匹配率预估（库内已有实体）──
        if db_index is not None and entity_names:
            stats = {"total": len(entity_names), "exact": 0,
                     "normalized": 0, "new": 0}
            ambiguous: list[dict] = []
            for nm in entity_names:
                if nm in exact_names:
                    stats["exact"] += 1
                elif normalize_name(nm) in norm_index:
                    stats["normalized"] += 1
                else:
                    stats["new"] += 1
                    if len(ambiguous) < max_ambiguous:
                        cands = match_candidates(nm, db_names, alias_re)
                        if cands:
                            ambiguous.append({"incoming": nm, "candidates": cands})
            entry["match_stats"] = stats
            entry["ambiguous"] = ambiguous

        # ── 导入建议 ──
        notes = []
        if tag_conflict:
            notes.append(f"sheet 名还含信号词 {'/'.join(tag_conflict)}，"
                         f"建议标签可能误判，请人工确认")
        if one_to_many and suggested_tag is not None:
            notes.append("该 sheet 疑似一对多明细，仅打标签安全，明细本身需子表承接")

        if is_relation_sheet:
            rec = "review"
            reason = ("关系表：sheet 名命中关系信号词且无强实体列，"
                      "疑似两个实体间的关系数据，不能并入单表属性，需单独决策")
        elif not name_cols and not useful and suggested_tag is None:
            rec, reason = "skip", "无实体名称列，且无映射/标签规则命中"
        elif suggested_tag and suggested_tag.get("value") is not None and not useful:
            rec = "tag_only"
            reason = (f"sheet 名命中标签 {suggested_tag['field']}"
                      f"={suggested_tag['value']}，只需解析实体名称列")
        elif useful:
            if one_to_many:
                rec = "review"
                reason = (f"虽有 {len(useful)} 个可映射列，但行数({len(rows)})"
                          f"远多于实体数({len(entity_names)})，疑似一对多明细，"
                          f"直接导入有标量覆盖风险，需人工确认")
            else:
                rec = "mapped"
                reason = f"{len(useful)} 个数据列可按建议映射，其余走 parse_sheet 人工核对"
                if suggested_tag and suggested_tag.get("value") is None:
                    reason += f"；另：标签 {suggested_tag['field']} 的值需从行内列提取（枚举值型）"
        elif suggested_tag and suggested_tag.get("value") is None:
            rec = "review"
            reason = (f"标签 {suggested_tag['field']} 的值需从行内列提取（枚举值型），"
                      f"需人工指定 col_map")
        else:
            rec = "review"
            reason = "含实体名称列但无规则命中（可能是一对多明细或未覆盖的专题名单）"
        if notes and rec != "review":
            reason += "；⚠ " + "；".join(notes)
        entry["recommendation"] = rec
        entry["reason"] = reason

        plan.append(entry)

    return plan
