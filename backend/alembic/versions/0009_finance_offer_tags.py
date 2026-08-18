"""Депозиты вводятся по тегам, а не по единственной строке SOK.

Под оффером теперь произвольное число именованных строк: SOK — просто первая из
них, её можно переименовать и завести рядом свои. Депозиты за день лежат на теге,
доход оффера — их сумма за день, умноженная на ставку.

Существующие SOK переезжают в тег с тем же именем, так что уже введённые месяцы
остаются на месте.
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0009_finance_offer_tags"
down_revision = "0008_finance_books"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_offer_tags",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "offer_id",
            sa.Uuid(),
            sa.ForeignKey("finance_book_offers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(120), nullable=False),
    )
    op.create_index("ix_finance_offer_tags_offer_id", "finance_offer_tags", ["offer_id"])

    op.create_table(
        "finance_tag_days",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tag_id",
            sa.Uuid(),
            sa.ForeignKey("finance_offer_tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("deposits", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.UniqueConstraint("tag_id", "day", name="uq_finance_tag_day"),
    )
    op.create_index("ix_finance_tag_days_tag_id", "finance_tag_days", ["tag_id"])

    bind = op.get_bind()
    if "finance_offer_days" not in sa.inspect(bind).get_table_names():
        return

    # Каждому офферу, у которого были дни, — тег «SOK», и его дни переезжают туда.
    old_days = sa.table(
        "finance_offer_days",
        sa.column("offer_id", sa.Uuid()),
        sa.column("day", sa.Integer()),
        sa.column("sok", sa.Numeric(18, 4)),
    )
    rows = bind.execute(sa.select(old_days)).mappings().all()
    by_offer: dict[uuid.UUID, list] = {}
    for row in rows:
        by_offer.setdefault(row["offer_id"], []).append(row)

    # Тег заводится каждому офферу, даже пустому: без него блок оффера остаётся
    # без единой строки ввода, и заполнять его будет некуда.
    offers = sa.table("finance_book_offers", sa.column("id", sa.Uuid()))
    for offer_id in bind.execute(sa.select(offers.c.id)).scalars():
        by_offer.setdefault(offer_id, [])

    tags, tag_days = [], []
    for offer_id, days in by_offer.items():
        tag_id = uuid.uuid4()
        tags.append({"id": tag_id, "offer_id": offer_id, "position": 0, "name": "SOK"})
        for row in days:
            tag_days.append(
                {
                    "id": uuid.uuid4(),
                    "tag_id": tag_id,
                    "day": row["day"],
                    "deposits": row["sok"],
                }
            )
    if tags:
        op.bulk_insert(_tags_table(), tags)
    if tag_days:
        op.bulk_insert(_tag_days_table(), tag_days)
    op.drop_table("finance_offer_days")


def _tags_table() -> sa.Table:
    return sa.table(
        "finance_offer_tags",
        sa.column("id", sa.Uuid()),
        sa.column("offer_id", sa.Uuid()),
        sa.column("position", sa.Integer()),
        sa.column("name", sa.String(120)),
    )


def _tag_days_table() -> sa.Table:
    return sa.table(
        "finance_tag_days",
        sa.column("id", sa.Uuid()),
        sa.column("tag_id", sa.Uuid()),
        sa.column("day", sa.Integer()),
        sa.column("deposits", sa.Numeric(18, 4)),
    )


def downgrade() -> None:
    op.create_table(
        "finance_offer_days",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "offer_id",
            sa.Uuid(),
            sa.ForeignKey("finance_book_offers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("sok", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.UniqueConstraint("offer_id", "day", name="uq_finance_offer_day"),
    )
    op.create_index("ix_finance_offer_days_offer_id", "finance_offer_days", ["offer_id"])
    bind = op.get_bind()
    # Обратно схлопываем в одну строку на оффер: несколько тегов складываются.
    bind.execute(
        sa.text(
            "INSERT INTO finance_offer_days (id, offer_id, day, sok) "
            "SELECT MIN(d.id), t.offer_id, d.day, SUM(d.deposits) "
            "FROM finance_tag_days d JOIN finance_offer_tags t ON t.id = d.tag_id "
            "GROUP BY t.offer_id, d.day"
        )
    )
    op.drop_table("finance_tag_days")
    op.drop_index("ix_finance_offer_tags_offer_id", table_name="finance_offer_tags")
    op.drop_table("finance_offer_tags")
