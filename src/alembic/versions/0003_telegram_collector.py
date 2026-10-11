"""telegram collector: raw payload, soft delete, dialogs, cursor as max msg_id

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

import inbox_hub.db.types
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UTC_DT = inbox_hub.db.types.UTCDateTime


def upgrade() -> None:
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("raw", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("deleted_at", UTC_DT(), nullable=True))
    op.create_table(
        "telegram_dialogs",
        sa.Column("chat_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("seen_at", UTC_DT(), nullable=False),
        sa.PrimaryKeyConstraint("chat_id"),
    )
    # channels.cursor becomes the highest msg_id stored per chat, so the
    # reconciler does not re-read history that is already in the database.
    op.execute(
        """
        UPDATE channels SET cursor = (
            SELECT MAX(channels.cursor, COALESCE(MAX(CAST(
                substr(items.external_key, instr(items.external_key, ':') + 1)
                AS INTEGER)), 0))
            FROM items
            WHERE items.channel_id = channels.id AND items.source = 'telegram'
        )
        WHERE source = 'telegram'
        """
    )


def downgrade() -> None:
    op.drop_table("telegram_dialogs")
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("raw")
