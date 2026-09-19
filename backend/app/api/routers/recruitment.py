"""Раздел «Рекрутинг»: прокси к Recruitment Service.

CRM ничего не хранит о кандидатах и запусках — это данные сервиса. Роутер
только проверяет право `recruitment.view` (пока раздел у одного администратора)
и пересылает запросы, подставляя общий секрет на бэкенде: токен сервиса не
должен попадать в браузер ни при каких условиях.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_permission
from app.models import KnowledgeAttachment, RecruitmentCandidate, User
from app.services import attachments, recruitment_pipeline, storage
from app.services.audit import audit
from app.services.recruitment import RecruitmentClient, RecruitmentError

router = APIRouter(prefix="/recruitment", tags=["recruitment"])


def _client() -> RecruitmentClient:
    return RecruitmentClient()


async def _call(handler, *args, **kwargs) -> Any:
    """Вызов к сервису с превращением его сбоев в понятный фронтенду 502."""
    try:
        return await handler(*args, **kwargs)
    except RecruitmentError as exc:
        return JSONResponse(status_code=502, content={"error": {"message": str(exc)}})


def _is_error(result: Any) -> bool:
    return isinstance(result, JSONResponse)


@router.get("/search-templates")
async def list_templates(
    current: User = Depends(require_permission("recruitment.view")),
):
    return await _call(_client().list_templates)


@router.post("/search-templates", status_code=201)
async def create_template(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("recruitment.view")),
):
    payload = await request.json()
    result = await _call(_client().create_template, payload)
    if _is_error(result):
        return result
    await audit(
        db, current, "recruitment.template_created",
        f"Создан шаблон поиска «{payload.get('name') or ''}»".strip(),
        request=request, entity_id=str(result.get("id") or ""),
    )
    return result


@router.put("/search-templates/{template_id}")
async def update_template(
    template_id: str,
    request: Request,
    current: User = Depends(require_permission("recruitment.view")),
):
    payload = await request.json()
    return await _call(_client().update_template, template_id, payload)


@router.delete("/search-templates/{template_id}")
async def delete_template(
    template_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("recruitment.view")),
):
    result = await _call(_client().delete_template, template_id)
    await audit(
        db, current, "recruitment.template_deleted",
        f"Удалён шаблон поиска {template_id}",
        request=request, entity_id=template_id,
    )
    return {"ok": True} if result is None else result


@router.post("/search-templates/{template_id}/run", status_code=202)
async def run_template(
    template_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("recruitment.view")),
):
    result = await _call(_client().run_template, template_id)
    if _is_error(result):
        return result
    await audit(
        db, current, "recruitment.search_started",
        f"Запущен поиск по шаблону {template_id}",
        request=request, entity_id=template_id,
    )
    return result


@router.get("/search-runs/{run_id}")
async def get_run(
    run_id: str,
    current: User = Depends(require_permission("recruitment.view")),
):
    return await _call(_client().get_run, run_id)


@router.get("/search-runs")
async def list_runs(
    search_template_id: str | None = None,
    current: User = Depends(require_permission("recruitment.view")),
):
    return await _call(_client().list_runs, search_template_id)


@router.get("/candidates")
async def list_candidates(
    source: str | None = None,
    via: str | None = None,
    search_template_id: str | None = None,
    min_score: int | None = None,
    review_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current: User = Depends(require_permission("recruitment.view")),
):
    return await _call(
        _client().list_candidates,
        source=source,
        via=via,
        template_id=search_template_id,
        min_score=min_score,
        review_status=review_status,
        limit=limit,
        offset=offset,
    )


@router.patch("/candidates/{candidate_id}/review")
async def review_candidate(
    candidate_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("recruitment.view")),
):
    payload = await request.json()
    decision = str(payload.get("decision") or "")
    if decision not in ("added", "skipped", "pending"):
        return JSONResponse(
            status_code=422,
            content={"error": {"message": "Решение: added, skipped или pending"}},
        )
    result = await _call(
        _client().review_candidate, candidate_id, decision, current.login
    )
    if _is_error(result):
        return result
    # Воронка найма живёт в CRM: сервис её не хранит. «Добавить» заводит
    # карточку на этапе «Скрининг», отмена решения — снимает её с доски.
    if decision == "added" and isinstance(result, dict):
        await recruitment_pipeline.upsert_from_external(
            db, current.workspace_id, result, added_by_id=current.id
        )
    else:
        await recruitment_pipeline.drop_external(
            db, current.workspace_id, candidate_id
        )
    labels = {
        "added": "Добавлен в кандидаты",
        "skipped": "Пропущен",
        "pending": "Возвращён в неразобранные",
    }
    await audit(
        db, current, "recruitment.candidate_reviewed",
        f"{labels[decision]}: кандидат {candidate_id}",
        request=request, entity_id=candidate_id,
    )
    await db.commit()
    return result


@router.get("/hh/status")
async def hh_status(current: User = Depends(require_permission("recruitment.view"))):
    return await _call(_client().hh_status)


@router.get("/hh/vacancies")
async def hh_vacancies(current: User = Depends(require_permission("recruitment.view"))):
    return await _call(_client().hh_vacancies)


@router.get("/hh/areas")
async def hh_areas(
    query: str = Query(default="", max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    current: User = Depends(require_permission("recruitment.view")),
):
    """Подсказки городов HH; внутренний токен остаётся на бэкенде CRM."""
    return await _call(_client().hh_areas, query, limit)


@router.get("/telegram/applications/{application_id}/file")
async def telegram_application_file(
    application_id: uuid.UUID,
    current: User = Depends(require_permission("recruitment.view")),
):
    """Открыть резюме из Telegram без передачи сервисного токена в браузер."""
    result = await _call(_client().telegram_file, str(application_id))
    if _is_error(result):
        return result
    content, media_type, disposition, redirect_url = result
    if redirect_url:
        return RedirectResponse(redirect_url, status_code=307)
    headers = {"X-Content-Type-Options": "nosniff"}
    if disposition:
        headers["Content-Disposition"] = disposition
    return Response(content=content or b"", media_type=media_type, headers=headers)


@router.post("/hh/connect")
async def hh_connect(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("recruitment.view")),
):
    result = await _call(_client().hh_connect)
    if _is_error(result):
        return result
    await audit(
        db, current, "recruitment.hh_connect_started",
        "Начато подключение HH-аккаунта",
        request=request,
    )
    return result


# --- воронка найма (на стороне CRM) -----------------------------------------
#
# Recruitment Service отдаёт находки и их разбор, но этапы найма сознательно не
# хранит. Всё, что ниже, — наша сторона: доска, этапы, ответственный, заметка.


@router.get("/pipeline")
async def pipeline_board(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("recruitment.view")),
):
    """Доска найма. Перед выдачей подтягивает то, что помечено `added` в сервисе.

    Без этого люди, отобранные до появления воронки (или помеченные в обход
    CRM), в «Кандидатах» просто не появились бы.
    """
    imported = 0
    added = await _call(
        _client().list_candidates, review_status="added", limit=200
    )
    if not _is_error(added) and isinstance(added, list):
        imported = await recruitment_pipeline.sync_added(
            db, current.workspace_id, added, added_by_id=current.id
        )
        if imported:
            await db.commit()
    payload = await recruitment_pipeline.board(db, current.workspace_id)
    # Сервис мог не ответить — доска всё равно открывается на своих данных, но
    # человек должен знать, что список может быть неполным.
    payload["service_available"] = not _is_error(added)
    payload["imported"] = imported
    return payload


@router.post("/pipeline", status_code=201)
async def create_pipeline_candidate(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("recruitment.view")),
):
    """Кандидат, заведённый руками.

    Человека находят не только поиском и откликом: приводят по рекомендации,
    пишут в личку, встречают на конференции. Такой кандидат живёт в той же
    воронке — иначе половина найма ведётся мимо доски.

    `external_id` у него свой, с префиксом `manual:`: он не приходит из сервиса
    рекрутинга, и синхронизация его не трогает.
    """
    payload = await request.json()
    name = str(payload.get("full_name") or "").strip()
    position = str(payload.get("position_title") or "").strip()
    telegram = str(payload.get("telegram_contact") or "").strip()
    # Имя в форме больше не спрашивают: кандидата узнают по позиции и нику,
    # а «Иванов Иван» в карточке всё равно дописывают уже внутри неё.
    if not name and not position and not telegram:
        raise HTTPException(status_code=422, detail="Укажите позицию или телеграм")
    stage = str(payload.get("stage") or "screening")
    if stage not in recruitment_pipeline.STAGES:
        raise HTTPException(status_code=422, detail="Неизвестный этап")
    owner_id = None
    raw_owner = str(payload.get("owner_id") or "").strip()
    if raw_owner:
        try:
            owner_id = uuid.UUID(raw_owner)
        except ValueError:
            raise HTTPException(status_code=422, detail="Некорректный ответственный") from None
        owner = await db.get(User, owner_id)
        if not owner or owner.workspace_id != current.workspace_id:
            raise HTTPException(status_code=422, detail="Ответственный не найден")
    row = RecruitmentCandidate(
        workspace_id=current.workspace_id,
        external_id="manual:" + uuid.uuid4().hex,
        stage=stage,
        source="manual",
        position_title=position[:300] or None,
        target_position=position[:300] or None,
        geo=str(payload.get("geo") or "").strip()[:160] or None,
        telegram_contact=telegram[:120] or None,
        external_url=str(payload.get("external_url") or "").strip()[:500] or None,
        interview_record=str(payload.get("interview_record") or "").strip()[:500] or None,
        note=str(payload.get("note") or "").strip() or None,
        profile={"full_name": name} if name else {},
        added_by_id=current.id,
        owner_id=owner_id,
    )
    db.add(row)
    await db.flush()
    await audit(
        db, current, "recruitment.candidate_created",
        f"Заведён кандидат «{name or position or telegram}»",
        request=request, entity_id=str(row.id),
    )
    await db.commit()
    await db.refresh(row)
    return recruitment_pipeline.serialize(
        row, {owner_id: owner.name} if owner_id and owner else {}
    )


@router.patch("/pipeline/{row_id}")
async def update_pipeline_candidate(
    row_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("recruitment.view")),
):
    """Этап, ответственный, заметка и поля карточки."""
    row = await db.get(RecruitmentCandidate, row_id)
    if not row or row.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Кандидат не найден")
    payload = await request.json()
    changed = []
    if "stage" in payload:
        stage = str(payload["stage"] or "")
        if stage not in recruitment_pipeline.STAGES:
            raise HTTPException(status_code=422, detail="Неизвестный этап")
        if stage != row.stage:
            row.stage = stage
            row.stage_changed_at = datetime.now(UTC)
            changed.append(recruitment_pipeline.STAGE_LABELS[stage])
    if "owner_id" in payload:
        raw = str(payload["owner_id"] or "").strip()
        if not raw:
            row.owner_id = None
        else:
            try:
                owner_id = uuid.UUID(raw)
            except ValueError:
                raise HTTPException(status_code=422, detail="Некорректный ответственный") from None
            owner = await db.get(User, owner_id)
            if not owner or owner.workspace_id != current.workspace_id:
                raise HTTPException(status_code=422, detail="Ответственный не найден")
            row.owner_id = owner_id
    if "note" in payload:
        row.note = str(payload["note"] or "").strip() or None
    # Поля карточки, которые ведёт команда, а не сервис рекрутинга.
    for field, limit in (
        ("target_position", 300), ("telegram_contact", 120), ("interview_record", 500)
    ):
        if field in payload:
            setattr(row, field, str(payload[field] or "").strip()[:limit] or None)
    if changed:
        await audit(
            db, current, "recruitment.stage_changed",
            f"«{row.position_title or row.external_id}» → {changed[0]}",
            request=request, entity_id=str(row.id),
        )
    await db.commit()
    await db.refresh(row)
    owners = {}
    if row.owner_id:
        owner = await db.get(User, row.owner_id)
        if owner:
            owners[owner.id] = owner.name
    return recruitment_pipeline.serialize(row, owners)


RECORD_URL = "/api/v1/recruitment/records"


@router.post("/pipeline/{row_id}/record", status_code=201)
async def upload_interview_record(
    row_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("recruitment.view")),
) -> dict:
    """Загрузить запись интервью в карточку кандидата.

    Файл сразу закрепляется за кандидатом: незакреплённые загрузки уборщик
    удаляет через сутки, а запись интервью должна пережить и переход этапа, и
    отпуск нанимающего.
    """
    row = await db.get(RecruitmentCandidate, row_id)
    if not row or row.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Кандидат не найден")
    attachment, stale = await attachments.receive_upload(db, current, file)
    attachment.candidate_id = row.id
    path = attachment.storage_path
    try:
        await db.flush()
        row.interview_record = f"{RECORD_URL}/{attachment.id}"
        await audit(
            db, current, "recruitment.record_uploaded",
            f"Запись интервью «{attachment.file_name}» — "
            f"{row.position_title or row.external_id}",
            request=request, entity_id=str(row.id),
        )
        await db.commit()
        await db.refresh(row)
    except Exception:
        await db.rollback()
        storage.remove(path)
        raise
    for gone in stale:
        storage.remove(gone.storage_path)
    owners = {}
    if row.owner_id:
        owner = await db.get(User, row.owner_id)
        if owner:
            owners[owner.id] = owner.name
    return recruitment_pipeline.serialize(row, owners)


@router.get("/records/{attachment_id}")
async def read_interview_record(
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("recruitment.view")),
) -> Response:
    """Отдать запись интервью.

    Через API, а не прямой ссылкой с диска: файл виден только своей команде, и
    ссылка не должна обходить проверку сессии.
    """
    attachment = await db.get(KnowledgeAttachment, attachment_id)
    if (
        not attachment
        or attachment.workspace_id != current.workspace_id
        or attachment.candidate_id is None
    ):
        raise HTTPException(status_code=404, detail="Файл не найден")
    try:
        path = storage.resolve(attachment.storage_path)
    except storage.StorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=path.read_bytes(),
        media_type=attachment.mime_type,
        headers={
            # nosniff обязателен: файл, который браузер сочтёт HTML, выполнит
            # скрипты в нашем домене.
            "Content-Disposition": storage.disposition(
                attachment.file_name, inline=True
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/pipeline/{row_id}")
async def remove_pipeline_candidate(
    row_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("recruitment.view")),
):
    """Убрать с доски.

    В сервисе находка остаётся разобранной: снимаем карточку найма, а не
    отменяем решение HR — иначе человек снова всплыл бы в «Откликах».
    """
    row = await db.get(RecruitmentCandidate, row_id)
    if not row or row.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Кандидат не найден")
    title = row.position_title or row.external_id
    # Не удаляем строку: в сервисе находка остаётся разобранной, и удалённая
    # карточка возвращалась бы на доску при следующем её открытии. Отметка
    # помнит решение CRM.
    row.removed_at = datetime.now(UTC)
    await audit(
        db, current, "recruitment.candidate_removed",
        f"Кандидат «{title}» убран с доски найма",
        request=request, entity_id=str(row_id),
    )
    await db.commit()
    return {"deleted": str(row_id)}
