"""inspection_hazard_import

Revision ID: i4d8e0a3b6c2
Revises: i4d8e1f7b3c2
Create Date: 2026-09-22

巡查隐患表改造，支持「总隐患汇总表」Excel 导入（一行多隐患拆分为一行一隐患）：
  新增业务关联键：
    enterprise_code       企业统一社会信用代码（与 enterprise 表业务关联，可空）
    enterprise_name       企业名称（Excel 原文，与 enterprise 表业务关联，可空）
  新增隐患/巡查字段：
    hazard_index          同批次同企业内隐患序号（1..N）
    hazard_count_total    该次检查隐患总数
    region                所属园区镇街
    industry              行业
    inspectors            检查人（多人顿号分隔）
    rect_responsible_name 整改责任人姓名（Excel 原文，不依赖 staff 外键）
    remark                备注
  结构调整：
    hazard_level 改为可空（Excel 无等级列，导入留空，agent 后续补）
    (inspection_batch, enterprise_name, hazard_index) 唯一约束（幂等导入身份键）
    inspection_batch / enterprise_code / enterprise_name / status 加索引
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'i4d8e0a3b6c2'
down_revision: Union[str, None] = 'i4d8e1f7b3c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 新增列（全部可空，存量 0 行，无回填压力）
    op.add_column('inspection_record', sa.Column('enterprise_code', sa.String(), nullable=True))
    op.add_column('inspection_record', sa.Column('enterprise_name', sa.String(), nullable=True))
    op.add_column('inspection_record', sa.Column('hazard_index', sa.Integer(), nullable=True))
    op.add_column('inspection_record', sa.Column('hazard_count_total', sa.Integer(), nullable=True))
    op.add_column('inspection_record', sa.Column('region', sa.String(), nullable=True))
    op.add_column('inspection_record', sa.Column('industry', sa.String(), nullable=True))
    op.add_column('inspection_record', sa.Column('inspectors', sa.String(), nullable=True))
    op.add_column('inspection_record', sa.Column('rect_responsible_name', sa.String(), nullable=True))
    op.add_column('inspection_record', sa.Column('remark', sa.String(), nullable=True))

    # 2) hazard_level 改可空（Excel 导入留空，agent 后续补 low/medium/high）
    op.alter_column('inspection_record', 'hazard_level',
                    existing_type=sa.String(), nullable=True)

    # 3) 索引（列表筛选/导入 resolve 高频路径）
    op.create_index('ix_inspection_record_inspection_batch',
                    'inspection_record', ['inspection_batch'])
    op.create_index('ix_inspection_record_enterprise_code',
                    'inspection_record', ['enterprise_code'])
    op.create_index('ix_inspection_record_enterprise_name',
                    'inspection_record', ['enterprise_name'])
    op.create_index('ix_inspection_record_status',
                    'inspection_record', ['status'])

    # 4) 幂等导入身份键：批次 + 企业 + 隐患序号
    op.create_unique_constraint(
        'uq_inspection_hazard_identity',
        'inspection_record',
        ['inspection_batch', 'enterprise_name', 'hazard_index'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_inspection_hazard_identity', 'inspection_record',
                       type_='unique')
    op.drop_index('ix_inspection_record_status', table_name='inspection_record')
    op.drop_index('ix_inspection_record_enterprise_name', table_name='inspection_record')
    op.drop_index('ix_inspection_record_enterprise_code', table_name='inspection_record')
    op.drop_index('ix_inspection_record_inspection_batch', table_name='inspection_record')
    op.alter_column('inspection_record', 'hazard_level',
                    existing_type=sa.String(), nullable=False)
    for col in ('remark', 'rect_responsible_name', 'inspectors', 'industry', 'region',
                'hazard_count_total', 'hazard_index', 'enterprise_name', 'enterprise_code'):
        op.drop_column('inspection_record', col)
