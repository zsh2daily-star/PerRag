"""enterprise_has_large_storage_tank

Revision ID: d5f0a2b3c4d5
Revises: c4d8e9f0a1b2
Create Date: 2026-09-17

企业「附加信息」中的「涉及大型储罐」由 extra jsonb 文本键提升为正式布尔列
has_large_storage_tank（默认 false）；前端编辑表单以开关呈现、列表以风险标记呈现。
历史描述文本不再保留（如 id=26「2只2.5m3（氮气、氩气各1只）」随 extra 键清理一并移除）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5f0a2b3c4d5'
down_revision: Union[str, None] = 'c4d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'enterprise',
        sa.Column(
            'has_large_storage_tank',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('enterprise', 'has_large_storage_tank')
