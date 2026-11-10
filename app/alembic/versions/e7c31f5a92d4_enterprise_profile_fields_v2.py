"""enterprise_profile_fields_v2

Revision ID: e7c31f5a92d4
Revises: 3219a020b23d
Create Date: 2026-09-14 10:20:00.000000

企业档案字段按「基础/规模/安全」三类重构：
  基础：新增 person_in_charge(企业负责人)/safety_officer(企业安全员)/
        asset_attribute(资产属性 韩资/台资/其他)；
        enterprise_category 枚举由 企业/个体 改为 劳密/厂中厂/小场所/其他；
        旧值 enterprise/individual 归为「其他」。
  规模：is_above_scale(bool) 改为 enterprise_scale(规上/规下)，true→规上，false 留空。
  安全：is_fire_key_unit(bool) 改为 fire_level(二级/三级/重点单位)，true→重点单位，false 留空；
        新增 天然气/注塑/危化品/危废品/粉尘爆炸/新能源电焊 6 个 bool 风险点；
        is_factory_in_factory(bool) 并入 enterprise_category=厂中厂 后删除。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7c31f5a92d4'
down_revision: Union[str, None] = '3219a020b23d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 新增的 6 个安全风险点 bool 列
_NEW_BOOL_COLUMNS = (
    'has_natural_gas',        # 天然气
    'is_injection_molding',   # 注塑企业
    'has_hazardous_chemicals',  # 危化品
    'has_hazardous_waste',     # 危废品
    'involves_dust_explosion',  # 粉尘爆炸
    'is_new_energy_welding',   # 新能源电焊
)

# 新增的可空文本/枚举列
_NEW_TEXT_COLUMNS = (
    'person_in_charge',   # 企业负责人
    'safety_officer',     # 企业安全员
    'asset_attribute',    # 资产属性
    'enterprise_scale',   # 企业规模
    'fire_level',         # 消防重点单位等级
)


def upgrade() -> None:
    # ── 1) 加新列（先加后迁移，避免数据丢失）──
    for col in _NEW_TEXT_COLUMNS:
        op.add_column('enterprise', sa.Column(col, sa.String(), nullable=True))
    for col in _NEW_BOOL_COLUMNS:
        op.add_column(
            'enterprise',
            sa.Column(col, sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    # ── 2) 历史数据迁移（旧列此时仍在）──
    # 企业类型：旧字典 enterprise/individual → 其他
    op.execute(
        "UPDATE enterprise SET enterprise_category = '其他' "
        "WHERE enterprise_category IN ('enterprise', 'individual')"
    )
    # 厂中厂 bool → 企业类型=厂中厂（勾选过的直接覆盖为厂中厂）
    op.execute(
        "UPDATE enterprise SET enterprise_category = '厂中厂' "
        "WHERE is_factory_in_factory = true"
    )
    # 规上 bool → 企业规模（true→规上，false 留空=未填报）
    op.execute(
        "UPDATE enterprise SET enterprise_scale = '规上' "
        "WHERE is_above_scale = true"
    )
    # 消防重点 bool → 消防等级（true→重点单位，false 留空）
    op.execute(
        "UPDATE enterprise SET fire_level = '重点单位' "
        "WHERE is_fire_key_unit = true"
    )

    # ── 3) 删除被枚举/类型替换的 3 个旧 bool 列 ──
    op.drop_column('enterprise', 'is_fire_key_unit')
    op.drop_column('enterprise', 'is_factory_in_factory')
    op.drop_column('enterprise', 'is_above_scale')


def downgrade() -> None:
    # ── 1) 恢复 3 个旧 bool 列 ──
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
        sa.Column('is_fire_key_unit', sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # ── 2) 枚举值回迁 bool ──
    op.execute(
        "UPDATE enterprise SET is_above_scale = true WHERE enterprise_scale = '规上'"
    )
    op.execute(
        "UPDATE enterprise SET is_fire_key_unit = true WHERE fire_level = '重点单位'"
    )
    op.execute(
        "UPDATE enterprise SET is_factory_in_factory = true "
        "WHERE enterprise_category = '厂中厂'"
    )
    # 企业类型回旧字典：其他→enterprise；厂中厂已映射 bool，类型置空；
    # 劳密/小场所 旧字典无对应，置空。
    op.execute(
        "UPDATE enterprise SET enterprise_category = "
        "CASE WHEN enterprise_category = '其他' THEN 'enterprise' ELSE NULL END "
        "WHERE enterprise_category IS NOT NULL"
    )

    # ── 3) 删除本次新增列 ──
    for col in _NEW_BOOL_COLUMNS:
        op.drop_column('enterprise', col)
    for col in _NEW_TEXT_COLUMNS:
        op.drop_column('enterprise', col)
