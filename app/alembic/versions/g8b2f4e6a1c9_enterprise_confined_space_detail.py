"""enterprise_confined_space_detail

Revision ID: g8b2f4e6a1c9
Revises: a7e9c1d4b8f2
Create Date: 2026-09-18

有限空间明细字段从 extra jsonb 固化为独立列：
  confined_space_count    有限空间数量
  confined_space_type     有限空间类型（详细文本）
  confined_space_workers  有限空间作业人数
  confined_space_hazards  有限空间危险因素
数据从 extra 的同名中文键迁移，迁移后从 extra 删除这 4 个键。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'g8b2f4e6a1c9'
down_revision: Union[str, None] = 'a7e9c1d4b8f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MIGRATION_MAP = {
    '有限空间数量': 'confined_space_count',
    '有限空间类型': 'confined_space_type',
    '有限空间作业人数': 'confined_space_workers',
    '有限空间危险因素': 'confined_space_hazards',
}


def upgrade() -> None:
    # 1) 加 4 个可空文本列
    for col in _MIGRATION_MAP.values():
        op.add_column('enterprise', sa.Column(col, sa.String(), nullable=True))

    # 2) 数据迁移：extra ->> '键' 写入新列（仅对有值的行）
    for extra_key, col in _MIGRATION_MAP.items():
        op.execute(f"""
            UPDATE enterprise
            SET {col} = (extra ->> '{extra_key}')
            WHERE extra IS NOT NULL
              AND jsonb_typeof(extra) = 'object'
              AND extra ? '{extra_key}'
              AND (extra ->> '{extra_key}') IS NOT NULL
              AND btrim(extra ->> '{extra_key}') <> ''
        """)

    # 3) 从 extra 删除已迁移的 4 个键
    op.execute("""
        UPDATE enterprise
        SET extra = extra
            - '有限空间数量'
            - '有限空间类型'
            - '有限空间作业人数'
            - '有限空间危险因素'
        WHERE extra IS NOT NULL
          AND jsonb_typeof(extra) = 'object'
          AND (
            extra ? '有限空间数量'
            OR extra ? '有限空间类型'
            OR extra ? '有限空间作业人数'
            OR extra ? '有限空间危险因素'
          )
    """)

    # 4) 清理空对象 extra
    op.execute("""
        UPDATE enterprise
        SET extra = NULL
        WHERE extra IS NOT NULL
          AND jsonb_typeof(extra) = 'object'
          AND (SELECT count(*) FROM jsonb_object_keys(extra)) = 0
    """)


def downgrade() -> None:
    # 回滚：把 4 列数据回写进 extra，再删列
    op.execute("""
        UPDATE enterprise
        SET extra = COALESCE(extra, '{}'::jsonb)
            || jsonb_build_object(
                '有限空间数量', COALESCE(confined_space_count, ''),
                '有限空间类型', COALESCE(confined_space_type, ''),
                '有限空间作业人数', COALESCE(confined_space_workers, ''),
                '有限空间危险因素', COALESCE(confined_space_hazards, '')
            )
        WHERE confined_space_count IS NOT NULL
           OR confined_space_type IS NOT NULL
           OR confined_space_workers IS NOT NULL
           OR confined_space_hazards IS NOT NULL
    """)
    for col in _MIGRATION_MAP.values():
        op.drop_column('enterprise', col)
