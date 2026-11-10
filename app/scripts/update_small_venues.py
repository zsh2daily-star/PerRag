"""一次性导入脚本：小场所.xlsx → 主表 enterprise 更新 + 9 家新建。

用法（在 rag-api 容器内执行）：
    docker exec -w /app -e PYTHONPATH=/app rag-api python /app/app/scripts/update_small_venues.py

Excel 路径：/app/data/uploads/小场所.xlsx（容器内挂载点）

逻辑：
  - Excel 表头在第 2 行，数据从第 3 行开始，49 行
  - 字段映射：22 项写入 enterprise.extra jsonb（按 Excel 值 upsert，不动现有「备注」「联系电话」等键）
  - 主表已有 code 的 → 更新 extra；code 字段以 Excel 为准（覆盖，含字符 I/数字 1 修正）
  - 主表无 code 但店招或营业执照名称匹配 → 按名称匹配，更新 extra + code
  - Excel 有但主表无档案 → 新建 enterprise（code/name/enterprise_category=['小场所']），
    person_in_charge 从 Excel col18 解析（姓名+电话）；safety_officer 留空
  - col18 场所消防安全员 已存在企业不写入（保留主表现有 person_in_charge/safety_officer）
"""
import asyncio
import re
from typing import Optional

import openpyxl
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import async_session_factory
from app.db.models import Enterprise


EXCEL_PATH = "/app/data/uploads/小场所.xlsx"


def _norm(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


# 22 项 extra 字段映射：Excel 列号 → extra 键名
EXTRA_FIELD_MAP = [
    (5, "场所状态"),
    (9, "从业人员数"),
    (10, "场所面积"),
    (11, "承载人数"),
    (12, "是否占用多层"),
    (13, "有无营业执照"),
    (15, "场所大类"),
    (16, "场所小类"),
    (17, "是否一店多证"),
    (24, "专业监管部门及监管事项"),
    (25, "属地管理部门"),
    (26, "安全巡查员"),
    (27, "消防指导员"),
    (30, "风险等级"),
    (35, "涉及疏散安全"),
    (36, "涉及消防安全"),
    (37, "涉及用电安全"),
    (38, "涉及瓶装液化气"),
    (39, "涉及管道天然气"),
    (40, "涉及其他燃料"),
    (41, "涉及装饰装修及危险作业"),
    (42, "涉及三合一场所"),
    (43, "涉及储存易燃易爆物品"),
]


# 解析 col18 "姓名（电话）" → (姓名, 电话)
_PERSON_PHONE_RE = re.compile(r'^(.*?)\s*[（(]\s*([\d\*\s]+)\s*[)）]\s*$')


def _parse_person_phone(text: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not text:
        return None, None
    s = text.strip()
    # 去掉末尾多余空格
    s = re.sub(r'\s+', '', s)
    m = _PERSON_PHONE_RE.match(s)
    if m:
        return m.group(1).strip() or None, m.group(2).strip() or None
    return s or None, None


def _parse_excel() -> list[dict]:
    wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
    ws = wb["场所数据"]
    rows: list[dict] = []
    for r in range(3, ws.max_row + 1):
        seq = ws.cell(r, 1).value
        if seq in (None, ''):
            continue
        shop_name = _norm(ws.cell(r, 2).value)
        lic_name = _norm(ws.cell(r, 3).value)  # 营业执照名称
        code = _norm(ws.cell(r, 14).value)
        # col18 场所消防安全员及联系方式（仅用于新建企业的 person_in_charge）
        col18 = _norm(ws.cell(r, 18).value)
        person, phone = _parse_person_phone(col18)

        # 22 项 extra
        extra: dict = {}
        for col, key in EXTRA_FIELD_MAP:
            v = _norm(ws.cell(r, col).value)
            if v:
                extra[key] = v

        rows.append({
            "seq": seq,
            "shop_name": shop_name,
            "lic_name": lic_name,  # 营业执照名称（主表 name 候选）
            "code": code,
            "person_from_excel": person,
            "phone_from_excel": phone,
            "extra": extra,
        })
    wb.close()
    return rows


async def main() -> None:
    rows = _parse_excel()
    print(f"Excel 解析得到 {len(rows)} 条记录")

    async with async_session_factory() as session:
        # 加载主表所有企业，Python 端过滤含「小场所」标签的
        all_rows = (await session.execute(
            select(Enterprise)
        )).scalars().all()
        all_ent = [e for e in all_rows if e.enterprise_category and any('小场所' in (c or '') for c in e.enterprise_category)]
        print(f"主表小场所企业 {len(all_ent)} 家")

        # 三套索引
        by_code: dict[str, Enterprise] = {}
        by_lic_name: dict[str, Enterprise] = {}  # 主表 name 精确
        by_shop_alias: dict[str, Enterprise] = {}  # 主表 name 中含括号注释的别名

        for ent in all_ent:
            if ent.code:
                by_code[ent.code.strip()] = ent
            if ent.name:
                by_lic_name[ent.name.strip()] = ent
                # 提取括号内的别名（如「妈妈手（盐城经济技术开发区妈妈手韩国料理店）」中的「盐城经济技术开发区妈妈手韩国料理店」）
                for m in re.finditer(r'[（(]([^（）()]+)[)）]', ent.name):
                    alias = m.group(1).strip()
                    if alias and alias not in by_shop_alias:
                        by_shop_alias[alias] = ent

        # 也要能匹配 Excel 中无 code 的 4 家：靠店招名称匹配
        # 已通过 by_shop_alias 覆盖部分（如 Excel 店招=极狐，主表 name=极狐 时 by_lic_name["极狐"] 直接匹配）

        matched_count = 0
        new_created = 0
        code_overridden = 0
        extra_keys_written = 0

        for row in rows:
            ent: Optional[Enterprise] = None
            # 1. code 精确匹配
            if row["code"]:
                ent = by_code.get(row["code"])
            # 2. 营业执照名称匹配
            if not ent and row["lic_name"]:
                ent = by_lic_name.get(row["lic_name"])
            # 3. 店招匹配（主表 name 或别名）
            if not ent and row["shop_name"]:
                ent = by_lic_name.get(row["shop_name"]) or by_shop_alias.get(row["shop_name"])

            if ent:
                # 已存在：更新 code（以 Excel 为准） + upsert 22 项 extra
                if row["code"] and ent.code != row["code"]:
                    ent.code = row["code"]
                    code_overridden += 1
                # 合并 extra：构造新 dict + flag_modified 显式标记 dirty
                existing_extra = dict(ent.extra or {})
                for k, v in row["extra"].items():
                    existing_extra[k] = v
                ent.extra = existing_extra
                flag_modified(ent, "extra")
                matched_count += 1
                extra_keys_written += len(row["extra"])
            else:
                # 新建
                name = row["lic_name"] or row["shop_name"]
                if not name:
                    print(f"  序号{row['seq']} 跳过：无名称")
                    continue
                new_extra = dict(row["extra"])
                # 新建企业保留备注=小场所
                new_extra.setdefault("备注", "小场所")
                ent_new = Enterprise(
                    name=name,
                    code=row["code"],
                    enterprise_category=["小场所"],
                    person_in_charge=row["person_from_excel"],
                    person_in_charge_phone=row["phone_from_excel"],
                    extra=new_extra,
                )
                session.add(ent_new)
                new_created += 1
                extra_keys_written += len(row["extra"])

        await session.commit()
        print(f"=== 执行结果 ===")
        print(f"  已存在企业更新: {matched_count} 家")
        print(f"  新建企业: {new_created} 家")
        print(f"  code 字段被覆盖: {code_overridden} 家")
        print(f"  extra 键写入总数: {extra_keys_written}")


if __name__ == "__main__":
    asyncio.run(main())
