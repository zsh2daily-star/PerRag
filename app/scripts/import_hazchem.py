"""一次性导入脚本：危化品使用企业.xlsx → hazardous_chemical 表。

用法（在 rag-api 容器内执行）：
    docker exec rag-api python /app/app/scripts/import_hazchem.py

Excel 路径：/app/data/uploads/危化品使用企业.xlsx（容器内挂载点）

逻辑：
  - 表头在第 2 行（序号/县（区）/镇（区、街道）/企业名称/行业类型/规模/
    危化品名称/危化品状态/储量/设计最大储量/年度使用量/危化品用途/
    存储方式/存储位置/是否构成重大危险源/备注）
  - 数据从第 3 行开始；同一企业的后续行 序号/企业名称 为空（合并单元格），
    需继承上一行的企业名/区域/行业等元信息。
  - 按 enterprise.name 精确匹配（大小写不敏感）回填 enterprise_id；
    匹配不到的记录仍保留，enterprise_id 留空。
  - 重复导入按 (enterprise_name, hazchem_name, storage_location) 幂等：
    已存在则更新，不重复建档。
"""
import asyncio
from typing import Optional

import openpyxl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import async_session_factory
from app.db.models import Enterprise, HazardousChemical


EXCEL_PATH = "/app/data/uploads/危化品使用企业.xlsx"


def _norm(v) -> Optional[str]:
    """规范化单元格值：None/空白 → None；其他 → strip 后 str。"""
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


async def _load_enterprise_name_map(session: AsyncSession) -> dict[str, int]:
    """加载 enterprise 表全部 (name, id)，键为小写名称（去掉括号后缀）。"""
    import re
    rows = (await session.execute(select(Enterprise.id, Enterprise.name))).all()
    name_map: dict[str, int] = {}
    for eid, name in rows:
        if not name:
            continue
        # 主键：小写完整名
        name_map[name.lower()] = eid
        # 同时按「去掉括号后缀」做别名键（应对主表带「（金属熔融）」等后缀而 Excel 无后缀）
        stripped = re.sub(r'[（(].*?[)）]\s*$', '', name).strip().lower()
        if stripped and stripped != name.lower():
            # 不覆盖已存在的精确名映射
            name_map.setdefault(stripped, eid)
    return name_map


def _parse_excel() -> list[dict]:
    """解析 Excel → 行字典列表（带继承的企业/区域等元信息）。"""
    wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
    ws = wb["Sheet1"]
    rows: list[dict] = []
    # 当前行所属企业的元信息（继承用）
    cur_ent: dict = {}
    for r in range(3, ws.max_row + 1):
        seq = ws.cell(r, 1).value
        ent_name = _norm(ws.cell(r, 4).value)
        # 序号非空表示新企业行开始；为空表示是上一企业的危化品行
        if seq is not None and seq != "":
            cur_ent = {
                "enterprise_name": ent_name or "",
                "region": _norm(ws.cell(r, 2).value),
                "town": _norm(ws.cell(r, 3).value),
                "industry": _norm(ws.cell(r, 5).value),
                "scale": _norm(ws.cell(r, 6).value),
            }
        # 危化品明细（每行独立）
        hazchem_name = _norm(ws.cell(r, 7).value)
        if not hazchem_name:
            # 整行空，跳过
            continue
        rows.append({
            **cur_ent,
            "hazchem_name": hazchem_name,
            "hazchem_state": _norm(ws.cell(r, 8).value),
            "reserve": _norm(ws.cell(r, 9).value),
            "max_reserve": _norm(ws.cell(r, 10).value),
            "annual_usage": _norm(ws.cell(r, 11).value),
            "usage": _norm(ws.cell(r, 12).value),
            "storage_method": _norm(ws.cell(r, 13).value),
            "storage_location": _norm(ws.cell(r, 14).value),
            "major_hazard": _norm(ws.cell(r, 15).value),
            "remark": _norm(ws.cell(r, 16).value),
        })
    wb.close()
    return rows


async def main() -> None:
    rows = _parse_excel()
    print(f"Excel 解析得到 {len(rows)} 条危化品记录")

    async with async_session_factory() as session:
        name_map = await _load_enterprise_name_map(session)
        print(f"企业主表加载 {len(name_map)} 家")

        created = updated = skipped = unmatched = 0
        for row in rows:
            ent_name = row.get("enterprise_name") or ""
            enterprise_id = name_map.get(ent_name.lower()) if ent_name else None
            if ent_name and enterprise_id is None:
                unmatched += 1

            # 幂等键：(enterprise_name, hazchem_name, storage_location)
            existing = (
                await session.execute(
                    select(HazardousChemical).where(
                        HazardousChemical.enterprise_name == ent_name,
                        HazardousChemical.hazchem_name == row["hazchem_name"],
                        (
                            HazardousChemical.storage_location == row.get("storage_location")
                            if row.get("storage_location")
                            else HazardousChemical.storage_location.is_(None)
                        ),
                    )
                )
            ).scalar_one_or_none()

            if existing:
                # 更新
                for k, v in row.items():
                    setattr(existing, k, v)
                existing.enterprise_id = enterprise_id
                await session.flush()
                updated += 1
            else:
                # 新增
                rec = HazardousChemical(
                    enterprise_name=ent_name or None,
                    enterprise_id=enterprise_id,
                    region=row.get("region"),
                    town=row.get("town"),
                    industry=row.get("industry"),
                    scale=row.get("scale"),
                    hazchem_name=row.get("hazchem_name"),
                    hazchem_state=row.get("hazchem_state"),
                    reserve=row.get("reserve"),
                    max_reserve=row.get("max_reserve"),
                    annual_usage=row.get("annual_usage"),
                    usage=row.get("usage"),
                    storage_method=row.get("storage_method"),
                    storage_location=row.get("storage_location"),
                    major_hazard=row.get("major_hazard"),
                    remark=row.get("remark"),
                )
                session.add(rec)
                await session.flush()
                created += 1

        await session.commit()
        print(f"导入完成：新增 {created}，更新 {updated}，跳过 {skipped}，未匹配企业 {unmatched} 家")


if __name__ == "__main__":
    asyncio.run(main())
