"""drop enterprise redundant columns —— 彻底收口数据源

inspection_record: enterprise_code, enterprise_name
factory_in_factory: lessor_name, tenant_name, lessor_contact_person, lessor_contact_phone, tenant_contact_person, tenant_contact_phone
hazardous_chemical: enterprise_name, region, industry, scale

FK 关联率已 100%（2026-10-16 自动建档 + 补 FK），冗余列不再需要。
"""
from alembic import op
import sqlalchemy as sa

revision = "r8d9e0f1a2b3"
down_revision = "r7e2f9a4b3c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 幂等：列可能已被人工提前 drop（2026-10-16 存量收口时直接 psql 执行过）
    op.execute("ALTER TABLE inspection_record DROP COLUMN IF EXISTS enterprise_code")
    op.execute("ALTER TABLE inspection_record DROP COLUMN IF EXISTS enterprise_name")

    for col in ("lessor_name", "tenant_name", "lessor_contact_person",
                "lessor_contact_phone", "tenant_contact_person", "tenant_contact_phone"):
        op.execute(f"ALTER TABLE factory_in_factory DROP COLUMN IF EXISTS {col}")

    op.execute("DROP INDEX IF EXISTS ix_hazardous_chemical_enterprise_name")
    for col in ("enterprise_name", "region", "industry", "scale"):
        op.execute(f"ALTER TABLE hazardous_chemical DROP COLUMN IF EXISTS {col}")


def downgrade() -> None:
    op.add_column("inspection_record", sa.Column("enterprise_code", sa.Text(), nullable=True))
    op.add_column("inspection_record", sa.Column("enterprise_name", sa.Text(), nullable=True))

    op.add_column("factory_in_factory", sa.Column("lessor_name", sa.Text(), nullable=True))
    op.add_column("factory_in_factory", sa.Column("tenant_name", sa.Text(), nullable=True))
    op.add_column("factory_in_factory", sa.Column("lessor_contact_person", sa.Text(), nullable=True))
    op.add_column("factory_in_factory", sa.Column("lessor_contact_phone", sa.Text(), nullable=True))
    op.add_column("factory_in_factory", sa.Column("tenant_contact_person", sa.Text(), nullable=True))
    op.add_column("factory_in_factory", sa.Column("tenant_contact_phone", sa.Text(), nullable=True))

    op.add_column("hazardous_chemical", sa.Column("enterprise_name", sa.Text(), nullable=True))
    op.add_column("hazardous_chemical", sa.Column("region", sa.Text(), nullable=True))
    op.add_column("hazardous_chemical", sa.Column("industry", sa.Text(), nullable=True))
    op.add_column("hazardous_chemical", sa.Column("scale", sa.Text(), nullable=True))
