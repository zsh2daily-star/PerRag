"""一次性：orphan 行的企业自动建档进 Enterprise 主表 + 补 FK。

跑一次即可。在 _cleanup_orphan_fk 之后跑。
"""
import sys
from sqlalchemy import text
from app.db.database import get_session_factory


async def main():
    factory = get_session_factory()
    async with factory() as s:
        # 拉 orphan（inspection_record 里 enterprise_id 空但 enterprise_name 不空）
        orph = (await s.execute(text("""
            SELECT DISTINCT enterprise_name, region, industry
            FROM inspection_record
            WHERE enterprise_id IS NULL AND enterprise_name IS NOT NULL AND enterprise_name != ''
        """))).all()
        print(f"orphan 唯一企业: {len(orph)} 家")

        # 拉已有 Enterprise 名称
        existing = (await s.execute(text("SELECT id, name FROM enterprise"))).all()
        name_to_id = {r[1]: r[0] for r in existing}

        created = 0
        for name, region, industry in orph:
            if name in name_to_id:
                continue
            # 最小字段集建档
            await s.execute(
                text("""INSERT INTO enterprise (name, region, industry, created_at, updated_at)
                         VALUES (:n, :r, :i, now(), now())"""),
                {"n": name, "r": region or None, "i": industry or None},
            )
            # 拿到新 id
            new_id = (await s.execute(
                text("SELECT id FROM enterprise WHERE name = :n ORDER BY id DESC LIMIT 1"), {"n": name}
            )).first()[0]
            name_to_id[name] = new_id
            created += 1
            print(f"  🆕 建档: {name} → id={new_id} (region={region}, industry={industry})")

        await s.commit()
        print(f"\n✅ 新建 {created} 家企业。开始补 FK...")

        # 补 inspection_record FK
        rows = (await s.execute(text(
            "SELECT id, enterprise_name FROM inspection_record "
            "WHERE enterprise_id IS NULL AND enterprise_name IS NOT NULL AND enterprise_name != ''"
        ))).all()
        fixed = 0
        for rec_id, name in rows:
            ent_id = name_to_id.get(name)
            if ent_id:
                await s.execute(
                    text("UPDATE inspection_record SET enterprise_id = :eid WHERE id = :rid"),
                    {"eid": ent_id, "rid": rec_id},
                )
                fixed += 1
        await s.commit()
        print(f"✅ inspection_record 补 FK: {fixed} 条，总计 inspection={len(rows)-fixed} 条全补齐。")


import asyncio
asyncio.run(main())
