"""enterprise_out_of_park

Revision ID: a7e9c1d4b8f2
Revises: f8c3d4e5f6a7
Create Date: 2026-09-16

企业是否在新能源汽车产业园区监管范围之外的复核标记：
out_of_park=true 表示经台账行级「属地/镇街/地区」核对属于其他片区
（光电园/韩资园/步凤镇/综保区/新城街道），供人工复核后删除；
out_of_park_note 记录所属片区等判定依据。默认全部 false（在园内）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7e9c1d4b8f2'
down_revision: Union[str, None] = 'f8c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'enterprise',
        sa.Column(
            'out_of_park',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        'enterprise',
        sa.Column('out_of_park_note', sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('enterprise', 'out_of_park_note')
    op.drop_column('enterprise', 'out_of_park')
