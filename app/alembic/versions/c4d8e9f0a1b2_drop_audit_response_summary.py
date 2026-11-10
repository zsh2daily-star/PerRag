"""drop_audit_response_summary

Revision ID: c4d8e9f0a1b2
Revises: b3d7a1c69e02
Create Date: 2026-09-16

审计日志表删除死列 response_summary（从未有任何代码写入，全库 0 条数据）。
request_payload 保留：企业增改/材料上传等写操作仍在记录提交字段。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d8e9f0a1b2'
down_revision: Union[str, None] = 'b3d7a1c69e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('audit_log', 'response_summary')


def downgrade() -> None:
    op.add_column(
        'audit_log',
        sa.Column('response_summary', sa.String(), nullable=True),
    )
