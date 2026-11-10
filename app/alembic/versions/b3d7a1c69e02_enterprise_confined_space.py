"""enterprise_confined_space

Revision ID: b3d7a1c69e02
Revises: e7c31f5a92d4
Create Date: 2026-09-15

企业导入方案（ReadMe/企业导入方案.md 决策 #7）：
新增有限空间枚举列 confined_space（地上/地下，可空=未填报），
由「有限空间企业」属性表打标签写入。不加 PG comment（项目约定，避免 drift）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3d7a1c69e02'
down_revision: Union[str, None] = 'e7c31f5a92d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('enterprise', sa.Column('confined_space', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('enterprise', 'confined_space')
