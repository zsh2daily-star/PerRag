"""一次性数据清洗脚本：按 enterprise_name 匹配 Enterprise 主表，补 FK。

用法：在后端容器里 `python -m scripts._cleanup_orphan_fk` 跑一次即可。
匹配策略：exact name 精确匹配；找不到的记录输出到 stderr 供人工介入。
"""
import asyncio
import sys

sys.path.insert(0, "/home/zshay/rag-project")

from sqlalchemy import text

from app.db.database import get_session_factory


async def main():
    factory = get_session_factory()
    async with factory() as s:
        # ── 1. 拉 Enterprise 主表 {name: id} ──
        rows = (await s.execute(text("SELECT id, name FROM enterprise"))).all()
        name_to_id = {r[1]: r[0] for r in rows}
        print(f"Enterprise 主表: {len(name_to_id)} 家")

        # ── 2. inspection_record: orphan 补 enterprise_id ──
        orph = (await s.execute(text(
            "SELECT id, enterprise_name, enterprise_code FROM inspection_record "
            "WHERE enterprise_id IS NULL AND enterprise_name IS NOT NULL AND enterprise_name != ''"
        ))).all()
        print(f"\ninspection_record orphan: {len(orph)} 条")
        fixed_inspection = 0
        for rec_id, name, code in orph:
            ent_id = name_to_id.get(name)
            if not ent_id and code:
                # 退一步：按 code 匹配
                r2 = (await s.execute(
                    text("SELECT id FROM enterprise WHERE code = :c"), {"c": code}
                )).first()
                ent_id = r2[0] if r2 else None
            if ent_id:
                await s.execute(
                    text("UPDATE inspection_record SET enterprise_id = :eid WHERE id = :rid"),
                    {"eid": ent_id, "rid": rec_id},
                )
                fixed_inspection += 1
                print(f"  ✅ inspection#{rec_id}: {name} → enterprise.id={ent_id}")
            else:
                print(f"  ❌ inspection#{rec_id}: {name!r} (code={code!r}) — 匹配不到 Enterprise，保留 orphan")

        # ── 3. factory_in_factory: lessor/tenant orphan ──
        for side in ("lessor", "tenant"):
            col_name = f"{side}_name"
            fk_col = f"{side}_enterprise_id"
            orph2 = (await s.execute(text(f"""
                SELECT id, {col_name} FROM factory_in_factory
                WHERE {fk_col} IS NULL AND {col_name} IS NOT NULL AND {col_name} != ''
            """))).all()
            if orph2:
                print(f"\nfactory_in_factory {side} orphan: {len(orph2)} 条")
                for rec_id, name in orph2:
                    ent_id = name_to_id.get(name)
                    if ent_id:
                        await s.execute(
                            text(f"UPDATE factory_in_factory SET {fk_col} = :eid WHERE id = :rid"),
                            {"eid": ent_id, "rid": rec_id},
                        )
                        print(f"  ✅ f_in_f#{rec_id} {side}: {name} → enterprise.id={ent_id}")
                    else:
                        print(f"  ❌ f_in_f#{rec_id} {side}: {name!r} — 匹配不到")

        # ── 4. hazardous_chemical: 同理 ──
        orph3 = (await s.execute(text(
            "SELECT id, enterprise_name FROM hazardous_chemical "
            "WHERE enterprise_id IS NULL AND enterprise_name IS NOT NULL AND enterprise_name != ''"
        ))).all()
        if orph3:
            print(f"\nhazardous_chemical orphan: {len(orph3)} 条")
            for rec_id, name in orph3:
                ent_id = name_to_id.get(name)
                if ent_id:
                    await s.execute(
                        text("UPDATE hazardous_chemical SET enterprise_id = :eid WHERE id = :rid"),
                        {"eid": ent_id, "rid": rec_id},
                    )
                    print(f"  ✅ hazchem#{rec_id}: {name} → enterprise.id={ent_id}")
                else:
                    print(f"  ❌ hazchem#{rec_id}: {name!r} — 匹配不到")

        await s.commit()
        print(f"\n✅ 清洗完成。inspection 补了 {fixed_inspection} 条。")


asyncio.run(main())
