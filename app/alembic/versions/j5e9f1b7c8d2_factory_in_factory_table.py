"""factory_in_factory_table

Revision ID: j5e9f1b7c8d2
Revises: i4d8e0a3b6c2
Create Date: 2026-09-23

厂中厂消防安全专项整治记录表（factory_in_factory）：
  每行 = 一对（出租方, 承租方）+ 该次专项整治的隐患与整改。
  出租方/承租方都关联 enterprise 表（enterprise_id FK），原文姓名保留。
  多条隐患以顿号分隔的文本存于 hazard_description；如需逐条统计可后续拆分。
  inspection_batch 聚合同批次（如 "2026-厂中厂消防"），与企业表/巡查表同套筛选协议。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'j5e9f1b7c8d2'
down_revision: Union[str, None] = 'i4d8e0a3b6c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'factory_in_factory',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('inspection_batch', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('region', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('address', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # ── 出租方 ──
        sa.Column('lessor_enterprise_id', sa.Integer(), nullable=True),
        sa.Column('lessor_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('lessor_contact_person', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('lessor_contact_phone', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('lessor_use_area', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('lessor_hazard_description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('lessor_rect_status', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # ── 承租方 ──
        sa.Column('tenant_enterprise_id', sa.Integer(), nullable=True),
        sa.Column('tenant_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('tenant_area', sa.Float(), nullable=True),
        sa.Column('tenant_contact_person', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('tenant_contact_phone', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('agreement_signed', sa.Boolean(), nullable=True),
        sa.Column('tenant_hazard_description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('tenant_rect_status', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # ── 专项整治信息（解析自备注）──
        sa.Column('site_category', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('inspection_date', sa.Date(), nullable=True),
        sa.Column('inspectors', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('rect_supervisor', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # ── 备注 ──
        sa.Column('remark', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['lessor_enterprise_id'], ['enterprise.id'], ),
        sa.ForeignKeyConstraint(['tenant_enterprise_id'], ['enterprise.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_factory_in_factory_inspection_batch',
        'factory_in_factory', ['inspection_batch'],
    )
    op.create_index(
        'ix_factory_in_factory_lessor_name',
        'factory_in_factory', ['lessor_name'],
    )
    op.create_index(
        'ix_factory_in_factory_tenant_name',
        'factory_in_factory', ['tenant_name'],
    )
    op.create_index(
        'ix_factory_in_factory_lessor_enterprise_id',
        'factory_in_factory', ['lessor_enterprise_id'],
    )
    op.create_index(
        'ix_factory_in_factory_tenant_enterprise_id',
        'factory_in_factory', ['tenant_enterprise_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_factory_in_factory_tenant_enterprise_id', table_name='factory_in_factory')
    op.drop_index('ix_factory_in_factory_lessor_enterprise_id', table_name='factory_in_factory')
    op.drop_index('ix_factory_in_factory_tenant_name', table_name='factory_in_factory')
    op.drop_index('ix_factory_in_factory_lessor_name', table_name='factory_in_factory')
    op.drop_index('ix_factory_in_factory_inspection_batch', table_name='factory_in_factory')
    op.drop_table('factory_in_factory')
