"""chat_message token usage —— 智能问答 token/费用统计

chat_message 新增 3 列，存 Hermes 上游 usage 帧的 token 用量：
    prompt_tokens / completion_tokens / total_tokens （均可空）

存量 assistant 消息（无 usage）保持 NULL，前端按 0 累计。
"""
from alembic import op
import sqlalchemy as sa

revision = "s9f1a2b3c4d5"
down_revision = "r8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_message ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER")
    op.execute("ALTER TABLE chat_message ADD COLUMN IF NOT EXISTS completion_tokens INTEGER")
    op.execute("ALTER TABLE chat_message ADD COLUMN IF NOT EXISTS total_tokens INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE chat_message DROP COLUMN IF EXISTS prompt_tokens")
    op.execute("ALTER TABLE chat_message DROP COLUMN IF EXISTS completion_tokens")
    op.execute("ALTER TABLE chat_message DROP COLUMN IF EXISTS total_tokens")
