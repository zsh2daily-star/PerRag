"""enterprise_category_to_array

Revision ID: l7a1b3c5d9e8
Revises: k6f0a2b9c3d4
Create Date: 2026-09-23

enterprise_category 由单值 text 改为多标签 text[]：
  - schema: ALTER COLUMN TYPE text[] USING ARRAY[enterprise_category]
  - 数据补全: factory_in_factory 关系表里的 78 家企业按角色追加
    '厂中厂出租' / '厂中厂承租' 标签（@> 防重），实现多标签效果
    （帝晖=劳密+厂中厂出租；信博=劳密+厂中厂承租 等）
  - downgrade 取首元素回单值（损失多标签信息，仅回滚 schema）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'l7a1b3c5d9e8'
down_revision: Union[str, None] = 'k6f0a2b9c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) schema: text → text[]，NULL 保持 NULL（不变成 [NULL]）
    op.execute(
        sa.text(
            """
            ALTER TABLE enterprise
            ALTER COLUMN enterprise_category TYPE text[]
            USING CASE
                WHEN enterprise_category IS NULL THEN NULL
                ELSE ARRAY[enterprise_category]
            END
            """
        )
    )

    # 2) 数据补全：出租方追加 '厂中厂出租' 标签（已存在则跳过）
    op.execute(
        sa.text(
            """
            UPDATE enterprise e
            SET enterprise_category =
                array_append(e.enterprise_category, '厂中厂出租'),
                updated_at = NOW()
            WHERE e.id IN (
                SELECT DISTINCT lessor_enterprise_id
                FROM factory_in_factory
                WHERE lessor_enterprise_id IS NOT NULL
            )
            AND NOT e.enterprise_category @> ARRAY['厂中厂出租']::text[]
            """
        )
    )
    # 3) 数据补全：承租方追加 '厂中厂承租' 标签（已存在则跳过）
    op.execute(
        sa.text(
            """
            UPDATE enterprise e
            SET enterprise_category =
                array_append(e.enterprise_category, '厂中厂承租'),
                updated_at = NOW()
            WHERE e.id IN (
                SELECT DISTINCT tenant_enterprise_id
                FROM factory_in_factory
                WHERE tenant_enterprise_id IS NOT NULL
            )
            AND NOT e.enterprise_category @> ARRAY['厂中厂承租']::text[]
            """
        )
    )


def downgrade() -> None:
    # 取首元素回单值（多标签信息丢失，仅回滚 schema）
    op.execute(
        sa.text(
            """
            ALTER TABLE enterprise
            ALTER COLUMN enterprise_category TYPE text
            USING enterprise_category[1]
            """
        )
    )
