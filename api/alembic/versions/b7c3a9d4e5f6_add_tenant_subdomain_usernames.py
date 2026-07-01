"""add_tenant_subdomain_usernames

Revision ID: b7c3a9d4e5f6
Revises: a4e9c7f6d2b1
Create Date: 2026-06-30 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c3a9d4e5f6"
down_revision: Union[str, None] = "a4e9c7f6d2b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("subdomain", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_organizations_subdomain",
        "organizations",
        ["subdomain"],
        unique=True,
    )

    op.add_column(
        "users",
        sa.Column("username", sa.String(length=100), nullable=True),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=False)
    op.create_unique_constraint(
        "uq_users_org_username",
        "users",
        ["organization_id", "username"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_org_username", "users", type_="unique")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")

    op.drop_index("ix_organizations_subdomain", table_name="organizations")
    op.drop_column("organizations", "subdomain")
