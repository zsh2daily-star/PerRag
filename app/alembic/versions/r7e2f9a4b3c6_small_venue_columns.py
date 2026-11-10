"""small_venue_columns

Revision ID: r7e2f9a4b3c6
Revises: q6b9d3f8e2a5
Create Date: 2026-10-13

企业主表新增 23 个小场所正式列（从 extra jsonb 迁移固化）：
  - 文本类：venue_status / staff_count / venue_area / capacity /
            venue_category_major / venue_category_minor /
            regulatory_dept / territorial_dept /
            venue_safety_inspector / venue_fire_instructor / venue_risk_level
  - 布尔类（可空三态：None=未填报 / True=是 / False=否）：
    is_multistory / has_business_license / is_multi_license /
    involves_evacuation / involves_fire_safety / involves_electrical /
    involves_lpg / involves_pipe_gas / involves_other_fuel /
    involves_decoration_work / involves_three_in_one / involves_flammable_storage

迁移后通过数据脚本把 extra 中的小场所键搬运到正式列并删除 extra 中对应键。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'r7e2f9a4b3c6'
down_revision: Union[str, None] = 'q6b9d3f8e2a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 文本类列 ──
    op.add_column('enterprise', sa.Column('venue_status', sa.String(), nullable=True))
    op.add_column('enterprise', sa.Column('staff_count', sa.String(), nullable=True))
    op.add_column('enterprise', sa.Column('venue_area', sa.String(), nullable=True))
    op.add_column('enterprise', sa.Column('capacity', sa.String(), nullable=True))
    op.add_column('enterprise', sa.Column('venue_category_major', sa.String(), nullable=True))
    op.add_column('enterprise', sa.Column('venue_category_minor', sa.String(), nullable=True))
    op.add_column('enterprise', sa.Column('regulatory_dept', sa.String(), nullable=True))
    op.add_column('enterprise', sa.Column('territorial_dept', sa.String(), nullable=True))
    op.add_column('enterprise', sa.Column('venue_safety_inspector', sa.String(), nullable=True))
    op.add_column('enterprise', sa.Column('venue_fire_instructor', sa.String(), nullable=True))
    op.add_column('enterprise', sa.Column('venue_risk_level', sa.String(), nullable=True))
    # ── 布尔类列（三态：None/True/False）──
    op.add_column('enterprise', sa.Column('is_multistory', sa.Boolean(), nullable=True))
    op.add_column('enterprise', sa.Column('has_business_license', sa.Boolean(), nullable=True))
    op.add_column('enterprise', sa.Column('is_multi_license', sa.Boolean(), nullable=True))
    op.add_column('enterprise', sa.Column('involves_evacuation', sa.Boolean(), nullable=True))
    op.add_column('enterprise', sa.Column('involves_fire_safety', sa.Boolean(), nullable=True))
    op.add_column('enterprise', sa.Column('involves_electrical', sa.Boolean(), nullable=True))
    op.add_column('enterprise', sa.Column('involves_lpg', sa.Boolean(), nullable=True))
    op.add_column('enterprise', sa.Column('involves_pipe_gas', sa.Boolean(), nullable=True))
    op.add_column('enterprise', sa.Column('involves_other_fuel', sa.Boolean(), nullable=True))
    op.add_column('enterprise', sa.Column('involves_decoration_work', sa.Boolean(), nullable=True))
    op.add_column('enterprise', sa.Column('involves_three_in_one', sa.Boolean(), nullable=True))
    op.add_column('enterprise', sa.Column('involves_flammable_storage', sa.Boolean(), nullable=True))
    # 索引：场所大类/小类/风险等级用于筛选
    op.create_index('ix_enterprise_venue_category_major', 'enterprise', ['venue_category_major'])
    op.create_index('ix_enterprise_venue_category_minor', 'enterprise', ['venue_category_minor'])
    op.create_index('ix_enterprise_venue_risk_level', 'enterprise', ['venue_risk_level'])


def downgrade() -> None:
    op.drop_index('ix_enterprise_venue_risk_level', table_name='enterprise')
    op.drop_index('ix_enterprise_venue_category_minor', table_name='enterprise')
    op.drop_index('ix_enterprise_venue_category_major', table_name='enterprise')
    for col in [
        'involves_flammable_storage', 'involves_three_in_one', 'involves_decoration_work',
        'involves_other_fuel', 'involves_pipe_gas', 'involves_lpg',
        'involves_electrical', 'involves_fire_safety', 'involves_evacuation',
        'is_multi_license', 'has_business_license', 'is_multistory',
        'venue_risk_level', 'venue_fire_instructor', 'venue_safety_inspector',
        'territorial_dept', 'regulatory_dept',
        'venue_category_minor', 'venue_category_major',
        'capacity', 'venue_area', 'staff_count', 'venue_status',
    ]:
        op.drop_column('enterprise', col)
