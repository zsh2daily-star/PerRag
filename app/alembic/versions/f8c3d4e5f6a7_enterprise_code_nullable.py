"""enterprise_code_nullable

Revision ID: f8c3d4e5f6a7
Revises: d5f0a2b3c4d5
Create Date: 2026-09-16

统一社会信用代码改为可空：源 Excel 中大量企业无信用代码（名称即身份），
NULL 表示未填报。Postgres 唯一约束允许多个 NULL；手工创建（API）仍强制
必填（service 校验不变），仅导入通道允许无代码企业。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f8c3d4e5f6a7'
down_revision: Union[str, None] = 'd5f0a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'enterprise', 'code',
        existing_type=sa.String(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        'enterprise', 'code',
        existing_type=sa.String(),
        nullable=False,
    )
