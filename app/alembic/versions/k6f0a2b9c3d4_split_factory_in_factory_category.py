"""split_factory_in_factory_category

Revision ID: k6f0a2b9c3d4
Revises: j5e9f1b7c8d2
Create Date: 2026-09-23

enterprise_category 枚举由 '厂中厂' 拆分为 '厂中厂出租' 与 '厂中厂承租' 两类：
  - 在 factory_in_factory 关系表中作为出租方的 → '厂中厂出租'
  - 在 factory_in_factory 关系表中作为承租方的 → '厂中厂承租'
  - 既在出租方又在承租方的 → 以出租方优先（出租方多为主业，承租为辅）
  - 历史标 '厂中厂' 但不在本次专项整治责任清单中的 → 默认 '厂中厂承租'
    （被监管企业多为承租方/生产方，无出租方角色信息默认归承租方）
同时清理 enterprise.extra 中的 '厂中厂角色' 键（角色信息已固化到 category 列）。

注：本次仅更新数据，schema 列类型仍为 text（无 CHECK 约束），无需 DDL。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'k6f0a2b9c3d4'
down_revision: Union[str, None] = 'j5e9f1b7c8d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 在关系表 - 出租方 → '厂中厂出租'（含同时是承租方的，出租方优先）
    op.execute(
        sa.text(
            """
            UPDATE enterprise
            SET enterprise_category = '厂中厂出租',
                updated_at = NOW()
            WHERE enterprise_category = '厂中厂'
              AND id IN (
                  SELECT DISTINCT lessor_enterprise_id
                  FROM factory_in_factory
                  WHERE lessor_enterprise_id IS NOT NULL
              )
            """
        )
    )
    # 2) 在关系表 - 承租方 → '厂中厂承租'（仅承租，未被上面出租方覆盖的）
    op.execute(
        sa.text(
            """
            UPDATE enterprise
            SET enterprise_category = '厂中厂承租',
                updated_at = NOW()
            WHERE enterprise_category = '厂中厂'
              AND id IN (
                  SELECT DISTINCT tenant_enterprise_id
                  FROM factory_in_factory
                  WHERE tenant_enterprise_id IS NOT NULL
              )
            """
        )
    )
    # 3) 剩余历史 '厂中厂'（不在专项整治责任清单中）默认 → '厂中厂承租'
    op.execute(
        sa.text(
            """
            UPDATE enterprise
            SET enterprise_category = '厂中厂承租',
                updated_at = NOW()
            WHERE enterprise_category = '厂中厂'
            """
        )
    )
    # 4) 清理 extra 中的 '厂中厂角色' 键（角色信息已固化到 category）
    op.execute(
        sa.text(
            """
            UPDATE enterprise
            SET extra = extra - '厂中厂角色',
                updated_at = NOW()
            WHERE extra ? '厂中厂角色'
            """
        )
    )


def downgrade() -> None:
    # 回滚：合并 '厂中厂出租' / '厂中厂承租' 回 '厂中厂'，并在 extra 标记角色
    op.execute(
        sa.text(
            """
            UPDATE enterprise
            SET enterprise_category = '厂中厂',
                updated_at = NOW()
            WHERE enterprise_category IN ('厂中厂出租', '厂中厂承租')
            """
        )
    )
    # 按关系表回填 extra["厂中厂角色"]
    op.execute(
        sa.text(
            """
            UPDATE enterprise e
            SET extra = COALESCE(e.extra, '{}'::jsonb) || jsonb_build_object(
                '厂中厂角色',
                CASE
                    WHEN EXISTS (
                        SELECT 1 FROM factory_in_factory f
                        WHERE f.lessor_enterprise_id = e.id
                    ) AND EXISTS (
                        SELECT 1 FROM factory_in_factory f
                        WHERE f.tenant_enterprise_id = e.id
                    )
                    THEN '出租方+承租方'
                    WHEN EXISTS (
                        SELECT 1 FROM factory_in_factory f
                        WHERE f.lessor_enterprise_id = e.id
                    )
                    THEN '出租方'
                    WHEN EXISTS (
                        SELECT 1 FROM factory_in_factory f
                        WHERE f.tenant_enterprise_id = e.id
                    )
                    THEN '承租方'
                END
            ),
            updated_at = NOW()
            WHERE e.enterprise_category = '厂中厂'
            """
        )
    )
