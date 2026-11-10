"""enterprise_hazchem_detail

Revision ID: h3c7d9f2a5b1
Revises: g8b2f4e6a1c9
Create Date: 2026-09-20

危化品明细字段从 extra jsonb 固化为独立列：
  hazchem_used_names     使用危化品名称（数组 → "、"拼接）
  hazchem_names          危化品名称（台账，数组 → "、"拼接）
  hazchem_op_subject     作业主体（自行/发包/自行和发包）
  hazchem_major_hazard   是否重大危险源
  hazchem_state          危化品状态（液态/固态/气态，可多值）
  hazchem_op_cycle       作业周期（月/年）
  hazchem_usage          危化品用途（生产/实验/其他，可多值）
  hazchem_annual_usage   年度使用量（数组 → "、"拼接）
  hazchem_storage        存储方式（储罐/危化品库/中间库/防爆柜，可多值）
  hazchem_reserve        危化品储量（数组 → "、"拼接）
  hazchem_fixed_workers  作业人员是否固定（是/否）
迁移后从 extra 删除这 11 个键。
顺带修正：有危化品数据但 has_hazardous_chemicals 未标记的行置 TRUE。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'h3c7d9f2a5b1'
down_revision: Union[str, None] = 'g8b2f4e6a1c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# extra 键 → 新列名
_MIGRATION_MAP = {
    '使用危化品名称': 'hazchem_used_names',
    '危化品名称': 'hazchem_names',
    '作业主体': 'hazchem_op_subject',
    '是否重大危险源': 'hazchem_major_hazard',
    '危化品状态': 'hazchem_state',
    '作业周期': 'hazchem_op_cycle',
    '危化品用途': 'hazchem_usage',
    '年度使用量': 'hazchem_annual_usage',
    '存储方式': 'hazchem_storage',
    '危化品储量': 'hazchem_reserve',
    '作业人员是否固定': 'hazchem_fixed_workers',
}


def _extra_key_to_col(extra_key: str, col: str) -> str:
    """数组 → string_agg('、')；标量 → 直接取文本；空串不写入。"""
    return f"""
        UPDATE enterprise
        SET {col} = CASE
            WHEN jsonb_typeof(extra -> '{extra_key}') = 'array' THEN (
                SELECT string_agg(btrim(v), '、')
                FROM jsonb_array_elements_text(extra -> '{extra_key}') AS v
                WHERE btrim(v) <> ''
            )
            ELSE NULLIF(btrim(extra ->> '{extra_key}'), '')
        END
        WHERE extra IS NOT NULL
          AND jsonb_typeof(extra) = 'object'
          AND extra ? '{extra_key}'
          AND (extra ->> '{extra_key}') IS NOT NULL
    """


def upgrade() -> None:
    # 1) 加 11 个可空文本列
    for col in _MIGRATION_MAP.values():
        op.add_column('enterprise', sa.Column(col, sa.String(), nullable=True))

    # 2) 数据迁移（数组拼接 / 标量直取）
    for extra_key, col in _MIGRATION_MAP.items():
        op.execute(_extra_key_to_col(extra_key, col))

    # 3) 修正布尔标记：有危化品数据但 has_hazardous_chemicals 未置真
    op.execute(f"""
        UPDATE enterprise
        SET has_hazardous_chemicals = TRUE
        WHERE (has_hazardous_chemicals IS NOT TRUE)
          AND (
            {' OR '.join(f"{col} IS NOT NULL" for col in _MIGRATION_MAP.values())}
          )
    """)

    # 4) 从 extra 删除已迁移的 11 个键
    key_list = ', '.join(f"'{k}'" for k in _MIGRATION_MAP)
    key_cond = ' OR '.join(f"extra ? '{k}'" for k in _MIGRATION_MAP)
    op.execute(f"""
        UPDATE enterprise
        SET extra = extra - ARRAY[{key_list}]::text[]
        WHERE extra IS NOT NULL
          AND jsonb_typeof(extra) = 'object'
          AND ({key_cond})
    """)

    # 5) 清理空对象 extra
    op.execute("""
        UPDATE enterprise
        SET extra = NULL
        WHERE extra IS NOT NULL
          AND jsonb_typeof(extra) = 'object'
          AND (SELECT count(*) FROM jsonb_object_keys(extra)) = 0
    """)


def downgrade() -> None:
    # 回滚：把列数据回写进 extra（文本按"、"拆回数组尽力而为），再删列
    for extra_key, col in _MIGRATION_MAP.items():
        op.execute(f"""
            UPDATE enterprise
            SET extra = COALESCE(extra, '{{}}'::jsonb)
                || jsonb_build_object('{extra_key}',
                    CASE WHEN {col} LIKE '%、%'
                         THEN to_jsonb(string_to_array({col}, '、'))
                         ELSE to_jsonb({col}) END)
            WHERE {col} IS NOT NULL
        """)
    for col in _MIGRATION_MAP.values():
        op.drop_column('enterprise', col)
