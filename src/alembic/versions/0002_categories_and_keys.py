"""categories, api keys, per-key receipts

Existing data moves into a `study` category and a `study` key (inactive until
`inbox-hub keys rotate study`), which inherits the old global Received marks.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10 12:00:00

"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

import inbox_hub.db.types
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UTC_DT = inbox_hub.db.types.UTCDateTime


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("auto_sources", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", UTC_DT(), nullable=False),
        sa.Column("revoked_at", UTC_DT(), nullable=True),
        sa.Column("last_used_at", UTC_DT(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_table(
        "channel_categories",
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("channel_id", "category_id"),
    )
    op.create_index(
        op.f("ix_channel_categories_category_id"),
        "channel_categories",
        ["category_id"],
        unique=False,
    )
    op.create_table(
        "api_key_categories",
        sa.Column("key_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["key_id"], ["api_keys.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("key_id", "category_id"),
    )
    op.create_table(
        "receipts",
        sa.Column("key_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("received_at", UTC_DT(), nullable=False),
        sa.ForeignKeyConstraint(["key_id"], ["api_keys.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("key_id", "item_id"),
    )
    op.create_index(op.f("ix_receipts_item_id"), "receipts", ["item_id"], unique=False)
    with op.batch_alter_table("channels", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "tracked", sa.Boolean(), server_default=sa.false(), nullable=False
            )
        )

    bind = op.get_bind()
    categories = sa.table(
        "categories",
        sa.column("id", sa.Integer),
        sa.column("slug", sa.String),
        sa.column("title", sa.String),
        sa.column("auto_sources", sa.JSON),
    )
    api_keys = sa.table(
        "api_keys",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("created_at", UTC_DT),
    )
    bind.execute(
        categories.insert().values(slug="study", title="Учёба", auto_sources=["sdo"])
    )
    bind.execute(api_keys.insert().values(name="study", created_at=datetime.now(UTC)))
    category_id = bind.scalar(sa.text("SELECT id FROM categories WHERE slug='study'"))
    key_id = bind.scalar(sa.text("SELECT id FROM api_keys WHERE name='study'"))
    params = {"category_id": category_id, "key_id": key_id}
    bind.execute(
        sa.text(
            "INSERT INTO channel_categories (channel_id, category_id) "
            "SELECT id, :category_id FROM channels"
        ),
        params,
    )
    bind.execute(
        sa.text(
            "INSERT INTO api_key_categories (key_id, category_id) "
            "VALUES (:key_id, :category_id)"
        ),
        params,
    )
    bind.execute(sa.text("UPDATE channels SET tracked = 1 WHERE source = 'telegram'"))
    bind.execute(
        sa.text(
            "INSERT INTO receipts (key_id, item_id, received_at) "
            "SELECT :key_id, id, received_at FROM items WHERE received_at IS NOT NULL"
        ),
        params,
    )

    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_items_received_at"))
        batch_op.drop_column("received_at")


def downgrade() -> None:
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("received_at", UTC_DT(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_items_received_at"), ["received_at"], unique=False
        )
    op.execute(
        "UPDATE items SET received_at = "
        "(SELECT MAX(received_at) FROM receipts WHERE receipts.item_id = items.id)"
    )
    with op.batch_alter_table("channels", schema=None) as batch_op:
        batch_op.drop_column("tracked")
    op.drop_index(op.f("ix_receipts_item_id"), table_name="receipts")
    op.drop_table("receipts")
    op.drop_table("api_key_categories")
    op.drop_index(
        op.f("ix_channel_categories_category_id"), table_name="channel_categories"
    )
    op.drop_table("channel_categories")
    op.drop_table("api_keys")
    op.drop_table("categories")
