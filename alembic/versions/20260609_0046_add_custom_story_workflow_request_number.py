"""add custom story workflow request number

Revision ID: 20260609_0046
Revises: 20260609_0045
Create Date: 2026-06-09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260609_0046"
down_revision: str | None = "20260609_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table_name = "custom_story_workflows"
    if not _table_exists(table_name):
        return

    if not _column_exists(table_name, "request_number"):
        op.add_column(table_name, sa.Column("request_number", sa.Integer(), nullable=True))
        bind = op.get_bind()
        dialect_name = bind.dialect.name
        if dialect_name in {"mysql", "mariadb"}:
            op.execute(
                """
                SET @custom_story_workflow_request_number := 0
                """
            )
            op.execute(
                """
                UPDATE custom_story_workflows
                SET request_number = (@custom_story_workflow_request_number := @custom_story_workflow_request_number + 1)
                ORDER BY created_at ASC, id ASC
                """
            )
        else:
            op.execute(
                """
                WITH numbered_workflows AS (
                    SELECT id, ROW_NUMBER() OVER (ORDER BY created_at ASC, id ASC) AS request_number
                    FROM custom_story_workflows
                )
                UPDATE custom_story_workflows
                SET request_number = (
                    SELECT numbered_workflows.request_number
                    FROM numbered_workflows
                    WHERE numbered_workflows.id = custom_story_workflows.id
                )
                """
            )

        op.alter_column(
            table_name,
            "request_number",
            existing_type=sa.Integer(),
            nullable=False,
        )

    _create_unique_constraint_if_missing(
        "uq_custom_story_workflows_request_number",
        table_name,
        ["request_number"],
    )


def downgrade() -> None:
    table_name = "custom_story_workflows"
    if not _table_exists(table_name):
        return
    _drop_constraint_if_exists("uq_custom_story_workflows_request_number", table_name)
    if _column_exists(table_name, "request_number"):
        op.drop_column(table_name, "request_number")


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _create_unique_constraint_if_missing(name: str, table_name: str, columns: list[str]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    constraints = inspector.get_unique_constraints(table_name) if inspector.has_table(table_name) else []
    if any(constraint.get("name") == name for constraint in constraints):
        return
    op.create_unique_constraint(name, table_name, columns)


def _drop_constraint_if_exists(name: str, table_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    constraints = inspector.get_unique_constraints(table_name) if inspector.has_table(table_name) else []
    if any(constraint.get("name") == name for constraint in constraints):
        op.drop_constraint(name, table_name, type_="unique")
