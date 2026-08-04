"""Baseline — establishes the migration contract, creates no tables.

martyrology-api has no application tables yet. This revision exists so that a
fresh `alembic upgrade head` succeeds against an empty `martyrology` database
and stamps a version, which is what the `api-migrate` compose service asserts.
The permission-request and notification subsystem adds real tables on top.

Revision ID: 0001_baseline
Revises:
"""

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
