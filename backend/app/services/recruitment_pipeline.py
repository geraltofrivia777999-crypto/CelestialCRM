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
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RecruitmentCandidate, User

# Порядок важен: в этом же порядке колонки стоят на доске.
STAGES = ("screening", "interview", "tech_interview", "offer", "hired", "rejected")
STAGE_LABELS = {
    "screening": "Скрининг",
    "interview": "Интервью",
    # Техническое интервью идёт после разговора с рекрутером: проверяют не
    # человека, а работу — связки, кабинеты, разбор кейсов.
    "tech_interview": "Тех. интервью",
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


def telegram_handle(url: str | None) -> str | None:
    """`https://t.me/leon_wp` → `@leon_wp`. Ссылка кандидата — единственное
    место, где ник вообще есть: отдельного поля сервис не отдаёт."""
    text = str(url or "").strip()
    if not text:
        return None
    tail = text.rsplit("/", 1)[-1].strip()
    if not tail:
        return None
    return tail if tail.startswith("@") else "@" + tail


def _text(value: object) -> str | None:
    return (str(value or "").strip() or None)


def resume_file_url(value: object) -> str | None:
    """Внутреннюю ссылку сервиса превращаем в защищённый маршрут CRM."""
    text = _text(value)
    if not text:
        return None
    parsed = urlparse(text)
    parts = parsed.path.strip("/").split("/")
    if len(parts) == 4 and parts[:2] == ["telegram", "applications"] and parts[3] == "file":
        try:
            application_id = uuid.UUID(parts[2])
        except ValueError:
            return text
        return f"/api/v1/recruitment/telegram/applications/{application_id}/file"
    return text


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
    telegram_source = next(
        (row for row in sources if str(row.get("source") or "") == "telegram"), None
    )
    return {
        "position_title": _text(profile.get("position_title")),
        "geo": _text(profile.get("geo")),
        "source": _text(first_source.get("source")),
        "tier": _text((best or {}).get("tier")),
        "score": _int_or_none((best or {}).get("score")),
        "salary_expectation": _money_or_none(profile.get("salary_expectation")),
        "experience_months": _int_or_none(profile.get("total_experience_months")),
        "external_url": _text(first_source.get("external_url")),
        # Показательная часть снимка. Новые ключи сервиса необязательны:
        # у старых кандидатов они пусты, и карточка просто не рисует строку.
        "profile": {
            key: value
            for key, value in {
                "full_name": _text(profile.get("full_name")),
                "birth_date": _text(profile.get("birth_date")),
                "age": _int_or_none(profile.get("age")),
                "telegram_username": telegram_handle(
                    (telegram_source or {}).get("external_url")
                ),
                "application_text": _text(profile.get("text_blob")),
                "resume_file_url": resume_file_url(
                    profile.get("resume_file_url") or profile.get("resume_url")
                ),
                "resume_file_name": _text(profile.get("resume_file_name")),
            }.items()
            if value is not None
        },
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
        _fill_telegram(row)
        # Кандидата убирали с доски, а теперь заводят снова — значит решение
        # передумали, и карточка возвращается.
        if row.removed_at is not None:
            row.removed_at = None
            row.stage_changed_at = datetime.now(UTC)
        return row
    row = RecruitmentCandidate(
        workspace_id=workspace_id,
        external_id=external_id,
        stage="screening",
        stage_changed_at=datetime.now(UTC),
        added_by_id=added_by_id,
        **fields,
    )
    _fill_telegram(row)
    db.add(row)
    await db.flush()
    return row


def _fill_telegram(row: RecruitmentCandidate) -> None:
    """Телеграм отклика подставляем сами — но только в пустое поле.

    У заявки из Telegram ник и есть единственный контакт, и вводить его руками
    там, где он уже известен, незачем. Введённое человеком не трогаем: он мог
    поправить ник на рабочий, а снимок сервиса про это не знает.
    """
    if row.telegram_contact:
        return
    handle = (row.profile or {}).get("telegram_username")
    if handle:
        row.telegram_contact = handle


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
        row.removed_at = datetime.now(UTC)


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
    # В «известные» попадают и убранные с доски: их сервис по-прежнему считает
    # разобранными, и без этого каждое открытие доски возвращало бы их назад.
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
                .where(
                    RecruitmentCandidate.workspace_id == workspace_id,
                    RecruitmentCandidate.removed_at.is_(None),
                )
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
        "profile": row.profile or {},
        "owner_id": str(row.owner_id) if row.owner_id else None,
        "owner_name": owners.get(row.owner_id) if row.owner_id else None,
        "note": row.note,
        "target_position": row.target_position,
        "telegram_contact": row.telegram_contact,
        "interview_record": row.interview_record,
        "stage_changed_at": row.stage_changed_at,
        "created_at": row.created_at,
    }
