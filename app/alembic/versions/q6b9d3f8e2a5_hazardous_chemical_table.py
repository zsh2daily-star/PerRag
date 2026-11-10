"""hazardous_chemical_table

Revision ID: q6b9d3f8e2a5
Revises: p5a8c2e7d1f4
Create Date: 2026-10-13

危化品使用企业专项整治记录表（hazardous_chemical）：
  每行 = 一家企业的一种危化品明细（名称/状态/储量/用途/存储等）。
  企业关联 enterprise 表（enterprise_id FK），原文姓名保留。
  前端按企业名纵向合并单元格展示（与厂中厂表同套筛选协议）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'q6b9d3f8e2a5'
down_revision: Union[str, None] = 'p5a8c2e7d1f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'hazardous_chemical',
        sa.Column('id', sa.Integer(), nullable=False),
        # ── 企业关联 ──
        sa.Column('enterprise_id', sa.Integer(), nullable=True),
        sa.Column('enterprise_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('region', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('town', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('industry', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('scale', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # ── 危化品明细 ──
        sa.Column('hazchem_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('hazchem_state', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('reserve', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('max_reserve', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('annual_usage', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('usage', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('storage_method', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('storage_location', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('major_hazard', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('remark', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprise.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_hazardous_chemical_enterprise_name',
        'hazardous_chemical', ['enterprise_name'],
    )
    op.create_index(
        'ix_hazardous_chemical_hazchem_name',
        'hazardous_chemical', ['hazchem_name'],
    )
    op.create_index(
        'ix_hazardous_chemical_enterprise_id',
        'hazardous_chemical', ['enterprise_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_hazardous_chemical_enterprise_id', table_name='hazardous_chemical')
    op.drop_index('ix_hazardous_chemical_hazchem_name', table_name='hazardous_chemical')
    op.drop_index('ix_hazardous_chemical_enterprise_name', table_name='hazardous_chemical')
    op.drop_table('hazardous_chemical')
