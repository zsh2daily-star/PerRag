"""enterprise_profile_fields

Revision ID: 3219a020b23d
Revises: a31543bbe23f
Create Date: 2026-09-14 00:44:46.370787

企业档案扩展：
  新增 7 个高频监管字段正式列（企业类型/规上/厂中厂/职工人数/年产值/宿舍/消防重点）；
  extra 由 varchar(JSON 文本) 升级为 jsonb（现有值全为 NULL，USING 兼容历史空串）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '3219a020b23d'
down_revision: Union[str, None] = 'a31543bbe23f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 新增高频监管字段（PG11+ 常量 default 加列不重写表，历史行自动取默认值）──
    # 项目约定：PG 列不带 comment（列含义写在 app/db/models.py 行内注释），
    # 否则 SQLModel metadata(无注释) 与库(有注释) 会产生 autogenerate drift。
    op.add_column(
        'enterprise',
        sa.Column('enterprise_category', sa.String(), nullable=True),
    )
    op.add_column(
        'enterprise',
        sa.Column('is_above_scale', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'enterprise',
        sa.Column('is_factory_in_factory', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'enterprise',
        sa.Column('employee_count', sa.Integer(), nullable=True),
    )
    op.add_column(
        'enterprise',
        sa.Column('annual_output_value', sa.Integer(), nullable=True),
    )
    op.add_column(
        'enterprise',
        sa.Column('has_dormitory', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'enterprise',
        sa.Column('is_fire_key_unit', sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # ── extra: varchar(JSON 文本) → jsonb ──
    # PG 不允许 varchar→jsonb 隐式转换，必须显式 USING；空串/NULL 归一为 NULL。
    op.alter_column(
        'enterprise',
        'extra',
        existing_type=sa.String(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=True,
        postgresql_using="CASE WHEN extra IS NULL OR extra = '' THEN NULL ELSE extra::jsonb END",
    )


def downgrade() -> None:
    # ── jsonb → varchar（JSON 文本），内容无损 ──
    op.alter_column(
        'enterprise',
        'extra',
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.String(),
        existing_nullable=True,
        postgresql_using="CASE WHEN extra IS NULL THEN NULL ELSE extra::text END",
    )
    # alter 类型不会清除列注释，而基线 extra 本无注释，显式清掉保证可逆
    op.execute("COMMENT ON COLUMN enterprise.extra IS NULL")
    # ── 删除 7 个新列 ──
    op.drop_column('enterprise', 'is_fire_key_unit')
    op.drop_column('enterprise', 'has_dormitory')
    op.drop_column('enterprise', 'annual_output_value')
    op.drop_column('enterprise', 'employee_count')
    op.drop_column('enterprise', 'is_factory_in_factory')
    op.drop_column('enterprise', 'is_above_scale')
    op.drop_column('enterprise', 'enterprise_category')
