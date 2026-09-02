"""Воронка найма: этапы, которые ведёт сама CRM.

Recruitment Service отвечает за находки и их разбор — «эту анкету HR уже
посмотрела или нет». Что происходит с человеком дальше — скрининг, интервью,
оффер, отказ — он сознательно не хранит, и правильно: это процесс компании, а
не поисковика резюме.

Здесь два движения навстречу друг другу:

* когда HR нажимает «Добавить в кандидаты», из ответа сервиса снимается профиль
  и заводится строка воронки на этапе «Скрининг»;
* когда доска открывается, всё, что помечено `added` в сервисе, но ещё не
  заведено у нас, подтягивается автоматически. Иначе люди, отобранные до
  появления воронки, остались бы невидимыми, а «Кандидаты» — пустыми при
  непустом сервисе.

Снимок профиля хранится у нас намеренно: доска обязана открываться и работать,
когда сервис рекрутинга недоступен.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RecruitmentCandidate, User

# Порядок важен: в этом же порядке колонки стоят на доске.
STAGES = ("screening", "interview", "offer", "hired", "rejected")
STAGE_LABELS = {
    "screening": "Скрининг",
    "interview": "Интервью",
    "offer": "Оффер",
    "hired": "Нанят",
    "rejected": "Отказ",
}
# Этапы, на которых работа с человеком закончена.
CLOSED_STAGES = {"hired", "rejected"}


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _money_or_none(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def snapshot(external: dict) -> dict:
    """Снимок находки сервиса — то, что показывает карточка на доске."""
    profile = external.get("parsed_profile") or {}
    sources = external.get("sources") or []
    scores = external.get("scores") or []
    best = None
    for score in scores:
        if best is None or (score.get("score") or 0) > (best.get("score") or 0):
            best = score
    first_source = sources[0] if sources else {}
    return {
        "position_title": (str(profile.get("position_title") or "") or None),
        "geo": (str(profile.get("geo") or "") or None),
        "source": (str(first_source.get("source") or "") or None),
        "tier": (str((best or {}).get("tier") or "") or None),
        "score": _int_or_none((best or {}).get("score")),
        "salary_expectation": _money_or_none(profile.get("salary_expectation")),
        "experience_months": _int_or_none(profile.get("total_experience_months")),
        "external_url": (str(first_source.get("external_url") or "") or None),
    }


async def upsert_from_external(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    external: dict,
    *,
    added_by_id: uuid.UUID | None,
) -> RecruitmentCandidate | None:
    """Завести кандидата в воронке или освежить его снимок.

    Этап у уже заведённого не трогаем: повторный разбор той же находки не должен
    возвращать человека с интервью обратно на скрининг.
    """
    external_id = str(external.get("id") or "").strip()
    if not external_id:
        return None
    row = await db.scalar(
        select(RecruitmentCandidate).where(
            RecruitmentCandidate.workspace_id == workspace_id,
            RecruitmentCandidate.external_id == external_id,
        )
    )
    fields = snapshot(external)
    if row:
        for key, value in fields.items():
            setattr(row, key, value)
        return row
    row = RecruitmentCandidate(
        workspace_id=workspace_id,
        external_id=external_id,
        stage="screening",
        stage_changed_at=datetime.now(UTC),
        added_by_id=added_by_id,
        **fields,
    )
    db.add(row)
    await db.flush()
    return row


async def drop_external(
    db: AsyncSession, workspace_id: uuid.UUID, external_id: str
) -> None:
    """Убрать из воронки — когда решение по находке отменили или её пропустили."""
    row = await db.scalar(
        select(RecruitmentCandidate).where(
            RecruitmentCandidate.workspace_id == workspace_id,
            RecruitmentCandidate.external_id == str(external_id),
        )
    )
    if row:
        await db.delete(row)


async def sync_added(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    externals: list[dict],
    *,
    added_by_id: uuid.UUID | None = None,
) -> int:
    """Подтянуть в воронку всё, что помечено `added` в сервисе.

    Нужно и при первом открытии доски (люди отобраны до появления воронки), и
    когда кого-то пометили `added` в обход CRM.
    """
    created = 0
    known = {
        row.external_id
        for row in (
            await db.execute(
                select(RecruitmentCandidate).where(
                    RecruitmentCandidate.workspace_id == workspace_id
                )
            )
        ).scalars()
    }
    for external in externals:
        external_id = str(external.get("id") or "").strip()
        if not external_id or external_id in known:
            continue
        await upsert_from_external(
            db, workspace_id, external, added_by_id=added_by_id
        )
        created += 1
    return created


async def board(db: AsyncSession, workspace_id: uuid.UUID) -> dict:
    """Доска: строки по этапам плюс счётчики колонок."""
    rows = list(
        (
            await db.execute(
                select(RecruitmentCandidate)
                .where(RecruitmentCandidate.workspace_id == workspace_id)
                .order_by(RecruitmentCandidate.stage_changed_at.desc())
            )
        ).scalars()
    )
    owner_ids = {row.owner_id for row in rows if row.owner_id}
    owners = {
        user.id: user.name
        for user in (
            await db.execute(select(User).where(User.id.in_(owner_ids or [uuid.uuid4()])))
        ).scalars()
    }
    return {
        "stages": [
            {
                "key": stage,
                "label": STAGE_LABELS[stage],
                "count": sum(1 for row in rows if row.stage == stage),
            }
            for stage in STAGES
        ],
        "items": [serialize(row, owners) for row in rows],
    }


def serialize(row: RecruitmentCandidate, owners: dict) -> dict:
    return {
        "id": str(row.id),
        "external_id": row.external_id,
        "stage": row.stage,
        "stage_label": STAGE_LABELS.get(row.stage, row.stage),
        "position_title": row.position_title,
        "geo": row.geo,
        "source": row.source,
        "tier": row.tier,
        "score": row.score,
        "salary_expectation": (
            float(row.salary_expectation) if row.salary_expectation is not None else None
        ),
        "experience_months": row.experience_months,
        "external_url": row.external_url,
        "owner_id": str(row.owner_id) if row.owner_id else None,
        "owner_name": owners.get(row.owner_id) if row.owner_id else None,
        "note": row.note,
        "stage_changed_at": row.stage_changed_at,
        "created_at": row.created_at,
    }
