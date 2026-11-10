"""enterprise_confined_space_op

Revision ID: i4d8e1f7b3c2
Revises: h3c7d9f2a5b1
Create Date: 2026-09-22

enterprise 表新增有限空间作业管理 3 列：
  confined_space_op_subject     作业主体（自行/发包等）
  confined_space_op_cycle       作业周期
  confined_space_fixed_workers  作业人员是否固定（是/否）

注：本迁移曾在 2026-09-22 经 watchfiles 热重载执行入库后原文件遗失，
DB 留有版本戳但脚本树缺文件（幻影版本）。2026-09-22 按实际已执行的
DDL 补建本文件，恢复 alembic 版本链完整性。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'i4d8e1f7b3c2'
down_revision: Union[str, None] = 'h3c7d9f2a5b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('enterprise',
                  sa.Column('confined_space_op_subject', sa.String(), nullable=True))
    op.add_column('enterprise',
                  sa.Column('confined_space_op_cycle', sa.String(), nullable=True))
    op.add_column('enterprise',
                  sa.Column('confined_space_fixed_workers', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('enterprise', 'confined_space_fixed_workers')
    op.drop_column('enterprise', 'confined_space_op_cycle')
    op.drop_column('enterprise', 'confined_space_op_subject')
