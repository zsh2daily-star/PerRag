"""enterprise_confined_space_to_array

Revision ID: p5a8c2e7d1f4
Revises: o3d4e5f6a7b8
Create Date: 2026-10-12

confined_space 由单值 text 改为多标签 text[]（企业可同时存在
地上 + 地下有限空间），对齐 enterprise_category 的多标签模式：
  - schema: ALTER COLUMN TYPE text[] USING ARRAY[confined_space]
  - 存量数据（地上 29 / 地下 1）原样包成单元素数组
  - downgrade 取首元素回单值（损失多标签信息，仅回滚 schema）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'p5a8c2e7d1f4'
down_revision: Union[str, None] = 'o3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # schema: text → text[]，NULL 保持 NULL（不变成 [NULL]）
    op.execute(
        sa.text(
            """
            ALTER TABLE enterprise
            ALTER COLUMN confined_space TYPE text[]
            USING CASE
                WHEN confined_space IS NULL THEN NULL
                ELSE ARRAY[confined_space]
            END
            """
        )
    )


def downgrade() -> None:
    # 取首元素回单值（多标签信息丢失，仅回滚 schema）
    op.execute(
        sa.text(
            """
            ALTER TABLE enterprise
            ALTER COLUMN confined_space TYPE text
            USING confined_space[1]
            """
        )
    )
