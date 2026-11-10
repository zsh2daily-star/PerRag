"""一次性数据迁移脚本：把 enterprise.extra 中的小场所 22 个键迁移到 23 个正式列。

用法（在 rag-api 容器内执行）：
    docker exec -w /app -e PYTHONPATH=/app rag-api python /app/app/scripts/migrate_small_venue_extra_to_columns.py

逻辑：
  - 仅对 enterprise_category 含「小场所」的企业处理
  - 字符串类字段：直接 extra[k] → 正式列（None 跳过）
  - 布尔类字段：extra[k] 解析「是/有」→ True，「否/无」→ False，其他→ None
  - 迁移完成后从 extra 中删除对应键（避免重复展示）
  - flag_modified 显式标记 extra 字段被修改
"""
import asyncio
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.db.database import async_session_factory
from app.db.models import Enterprise


# 22 个 extra 键 → 23 个正式列的映射（venue_status 等文本类 + 布尔类）
# 注：用户列了「统一社会信用代码」但已在主表 code 字段，不重复处理
STRING_FIELDS = [
    ("场所状态", "venue_status"),
    ("从业人员数", "staff_count"),
    ("场所面积", "venue_area"),
    ("承载人数", "capacity"),
    ("场所大类", "venue_category_major"),
    ("场所小类", "venue_category_minor"),
    ("专业监管部门及监管事项", "regulatory_dept"),
    ("属地管理部门", "territorial_dept"),
    ("安全巡查员", "venue_safety_inspector"),
    ("消防指导员", "venue_fire_instructor"),
    ("风险等级", "venue_risk_level"),
]

# 布尔字段：extra 值「是/有」→ True，「否/无」→ False
BOOL_FIELDS = [
    ("是否占用多层", "is_multistory"),
    ("有无营业执照", "has_business_license"),
    ("是否一店多证", "is_multi_license"),
    ("涉及疏散安全", "involves_evacuation"),
    ("涉及消防安全", "involves_fire_safety"),
    ("涉及用电安全", "involves_electrical"),
    ("涉及瓶装液化气", "involves_lpg"),
    ("涉及管道天然气", "involves_pipe_gas"),
    ("涉及其他燃料", "involves_other_fuel"),
    ("涉及装饰装修及危险作业", "involves_decoration_work"),
    ("涉及三合一场所", "involves_three_in_one"),
    ("涉及储存易燃易爆物品", "involves_flammable_storage"),
]


def _to_bool(v: Optional[str]) -> Optional[bool]:
    if v is None:
        return None
    s = str(v).strip()
    if s in ("是", "有", "true", "True", "TRUE"):
        return True
    if s in ("否", "无", "false", "False", "FALSE"):
        return False
    return None


async def main() -> None:
    async with async_session_factory() as session:
        all_rows = (await session.execute(select(Enterprise))).scalars().all()
        small = [e for e in all_rows if e.enterprise_category and any('小场所' in (c or '') for c in e.enterprise_category)]
        print(f"小场所企业总数: {len(small)}")

        migrated = 0
        extra_keys_removed_total = 0
        for ent in small:
            extra = dict(ent.extra or {})
            changed = False
            # 文本类迁移
            for k, col in STRING_FIELDS:
                if k in extra and extra[k]:
                    setattr(ent, col, extra[k])
                    changed = True
            # 布尔类迁移
            for k, col in BOOL_FIELDS:
                if k in extra:
                    b = _to_bool(extra[k])
                    if b is not None:
                        setattr(ent, col, b)
                        changed = True
            # 删除 extra 中已迁移的键（保留「备注」「联系电话」「主要安全风险」「国民经济分类」等其他键）
            for k, _ in STRING_FIELDS + BOOL_FIELDS:
                if k in extra:
                    del extra[k]
                    extra_keys_removed_total += 1
            ent.extra = extra
            flag_modified(ent, "extra")
            if changed:
                migrated += 1

        await session.commit()
        print(f"=== 迁移结果 ===")
        print(f"  实际更新字段的企业: {migrated} 家")
        print(f"  extra 中删除的键总数: {extra_keys_removed_total}")


if __name__ == "__main__":
    asyncio.run(main())
