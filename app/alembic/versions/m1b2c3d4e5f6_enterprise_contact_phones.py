"""enterprise_contact_phones

Revision ID: m1b2c3d4e5f6
Revises: l7a1b3c5d9e8
Create Date: 2026-09-23

enterprise 表新增 3 个联系电话列（与厂中厂 lessor/tenant_contact_phone 对齐，
让 4 个表格的「姓名 + 联系方式」字段表达一致）：
  legal_person_phone        法人电话
  person_in_charge_phone    企业负责人电话
  safety_officer_phone      安全员电话
全部可空、String 类型。存量数据无电话，置 NULL。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'm1b2c3d4e5f6'
down_revision: Union[str, None] = 'l7a1b3c5d9e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLS = (
    "legal_person_phone",
    "person_in_charge_phone",
    "safety_officer_phone",
)


def upgrade() -> None:
    for col in _NEW_COLS:
        op.add_column('enterprise', sa.Column(col, sa.String(), nullable=True))


def downgrade() -> None:
    for col in reversed(_NEW_COLS):
        op.drop_column('enterprise', col)
