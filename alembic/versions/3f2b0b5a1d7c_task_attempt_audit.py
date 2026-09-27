"""task attempt audit records

Revision ID: 3f2b0b5a1d7c
Revises: cf1c62d50694
Create Date: 2026-09-27

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "3f2b0b5a1d7c"
down_revision = "cf1c62d50694"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_attempt",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("task_execution_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "status_before",
            postgresql.ENUM(
                "PENDING",
                "QUEUED",
                "RUNNING",
                "RETRYING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "PARTIAL",
                name="taskstatus",
                create_constraint=True,
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status_after",
            postgresql.ENUM(
                "PENDING",
                "QUEUED",
                "RUNNING",
                "RETRYING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "PARTIAL",
                name="taskstatus",
                create_constraint=True,
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("error_detail", sa.String(), nullable=True),
        sa.Column("dispatch_result", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "dispatch_result IN ('blocked', 'dispatched', 'dispatch_failed', 'pending', 'shadowed')",
            name="ck_task_attempt_dispatch_result",
        ),
        sa.ForeignKeyConstraint(["task_execution_id"], ["task_execution.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_attempt_task_execution_id", "task_attempt", ["task_execution_id"], unique=False)
    op.create_index("ix_task_attempt_tenant_id", "task_attempt", ["tenant_id"], unique=False)
    op.create_index(
        "ix_task_attempt_task_execution_created",
        "task_attempt",
        ["task_execution_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_task_attempt_tenant_created", "task_attempt", ["tenant_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_task_attempt_tenant_created", table_name="task_attempt")
    op.drop_index("ix_task_attempt_task_execution_created", table_name="task_attempt")
    op.drop_index("ix_task_attempt_tenant_id", table_name="task_attempt")
    op.drop_index("ix_task_attempt_task_execution_id", table_name="task_attempt")
    op.drop_table("task_attempt")
