"""chat_tables

Revision ID: n2c3d4e5f6a7
Revises: m1b2c3d4e5f6
Create Date: 2026-10-02

智能问答（Hermes 流式代理）两张新表：
  chat_session  问答会话（UUID 主键，user_id → staff.id，按用户列会话）
  chat_message  问答消息（role=user/assistant，session_id 级联删除）
Hermes API 无状态，多轮上下文由 rag-api 从这两张表组装。
工具进度事件（hermes.tool.progress）只实时透传，不入库。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'n2c3d4e5f6a7'
down_revision: Union[str, None] = 'm1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chat_session',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False, server_default='新对话'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_chat_session_user_id', 'chat_session', ['user_id'])
    op.create_index('ix_chat_session_updated_at', 'chat_session', ['updated_at'])

    op.create_table(
        'chat_message',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'session_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('chat_session.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    # 拉取会话历史的固定访问模式：WHERE session_id=? ORDER BY created_at
    op.create_index(
        'ix_chat_message_session_created', 'chat_message', ['session_id', 'created_at']
    )


def downgrade() -> None:
    op.drop_index('ix_chat_message_session_created', table_name='chat_message')
    op.drop_table('chat_message')
    op.drop_index('ix_chat_session_updated_at', table_name='chat_session')
    op.drop_index('ix_chat_session_user_id', table_name='chat_session')
    op.drop_table('chat_session')
