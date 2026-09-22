"""Разово проставить теги пользователей под офферами в уже заведённых книгах.

Новые назначения получают теги сами (см. `finance_pull`). Этот скрипт нужен
один раз — для офферов, заведённых до появления тегов:

    python -m app.backfill_finance_tags              # текущий месяц
    python -m app.backfill_finance_tags --month 2026-09
    python -m app.backfill_finance_tags --dry-run    # только показать

Для каждого оффера в книге баера каждый его тег, которого под оффером ещё
нет, занимает первую безымянную строку (депозиты в ней остаются), а если
таких нет — встаёт новой строкой в конец. Уже названные строки не меняются,
поэтому повторный запуск ничего не дублирует.
"""

import argparse
import asyncio

from sqlalchemy import select

from app.core.clock import business_today
from app.core.database import SessionLocal
from app.models import FinanceBook, User
from app.services.finance_pull import add_tags_to_book_offers
from app.services.user_tags import normalize_tags


async def backfill(year: int, month: int, dry_run: bool) -> None:
    async with SessionLocal() as db:
        users = {
            user_id: (name, normalize_tags(tags or []))
            for user_id, name, tags in (
                await db.execute(select(User.id, User.login, User.finance_tags))
            ).all()
        }
        books = list(
            (
                await db.execute(
                    select(FinanceBook).where(
                        FinanceBook.year == year, FinanceBook.month == month
                    )
                )
            ).scalars()
        )
        renamed = added = 0
        for book in books:
            login, tags = users.get(book.buyer_id, ("?", []))
            if not tags:
                continue
            for offer_name, action, tag in await add_tags_to_book_offers(db, book.id, tags):
                if action == "rename":
                    renamed += 1
                else:
                    added += 1
                print(f"{login} · {book.tier} · {offer_name}: {action} «{tag}»")
        if dry_run:
            await db.rollback()
            print(f"Dry run: would rename {renamed}, add {added} tag rows")
        else:
            await db.commit()
            print(f"Done: renamed {renamed}, added {added} tag rows")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--month", help="YYYY-MM, по умолчанию текущий месяц")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.month:
        year, month = (int(part) for part in args.month.split("-"))
    else:
        today = business_today()
        year, month = today.year, today.month
    asyncio.run(backfill(year, month, args.dry_run))


if __name__ == "__main__":
    main()
