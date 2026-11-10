"""inspection_notice_number

Revision ID: o3d4e5f6a7b8
Revises: n2c3d4e5f6a7
Create Date: 2026-10-09

巡查记录新增交办单文书编号字段：
  notice_number  交办单/检查单编号（如 AHJ032026208），可空，默认空，
                 同批次记录共享，业务层编辑时按 inspection_batch 批量同步。

同时补齐 review_date 列：该列此前通过直接 SQL 加在存量库上、未经过
Alembic，此处用 ADD COLUMN IF NOT EXISTS 保证全新库也能由迁移建出，
且不会在已有该列的存量库上报错。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'o3d4e5f6a7b8'
down_revision: Union[str, None] = 'n2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS：兼容已直接加过 review_date 的存量库 + 全新库
    op.execute(
        'ALTER TABLE inspection_record '
        'ADD COLUMN IF NOT EXISTS review_date DATE'
    )
    op.execute(
        'ALTER TABLE inspection_record '
        'ADD COLUMN IF NOT EXISTS notice_number VARCHAR'
    )
    op.create_index('ix_inspection_record_notice_number',
                    'inspection_record', ['notice_number'])


def downgrade() -> None:
    op.drop_index('ix_inspection_record_notice_number',
                  table_name='inspection_record')
    op.drop_column('inspection_record', 'notice_number')
    # review_date 不在本迁移前的迁移链中，downgrade 时一并移除
    op.drop_column('inspection_record', 'review_date')
