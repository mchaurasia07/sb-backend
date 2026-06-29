"""make Razorpay subscription and payment references unique

Revision ID: 20260629_0070
Revises: 20260628_0069
Create Date: 2026-06-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260629_0070"
down_revision: str | None = "20260628_0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UNIQUE_REFERENCES = (
    (
        "child_subscriptions",
        "provider_subscription_id",
        "ux_child_subscription_provider_sub",
    ),
    (
        "purchase_orders",
        "provider_subscription_id",
        "ux_purchase_provider_sub",
    ),
    ("payments", "provider_payment_id", "ux_payment_provider_payment"),
)


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    table_names = set(inspector.get_table_names())

    for table_name, column_name, index_name in UNIQUE_REFERENCES:
        if table_name not in table_names:
            continue
        duplicate = connection.execute(
            sa.text(
                f"SELECT {column_name} FROM {table_name} "
                f"WHERE {column_name} IS NOT NULL "
                f"GROUP BY {column_name} HAVING COUNT(*) > 1 LIMIT 1"
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise RuntimeError(
                f"Cannot add {index_name}: duplicate {table_name}.{column_name} "
                f"value {duplicate!r} must be reconciled first."
            )
        existing = {item["name"] for item in inspector.get_indexes(table_name)}
        if index_name not in existing:
            op.create_index(
                index_name,
                table_name,
                [column_name],
                unique=True,
            )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    table_names = set(inspector.get_table_names())

    for table_name, _column_name, index_name in reversed(UNIQUE_REFERENCES):
        if table_name not in table_names:
            continue
        existing = {item["name"] for item in inspector.get_indexes(table_name)}
        if index_name in existing:
            op.drop_index(index_name, table_name=table_name)
