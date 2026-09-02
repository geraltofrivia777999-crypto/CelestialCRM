"""Раздел «Рекрутинг»: прокси к Recruitment Service.

CRM ничего не хранит о кандидатах и запусках — это данные сервиса. Роутер
только проверяет право `recruitment.view` (пока раздел у одного администратора)
и пересылает запросы, подставляя общий секрет на бэкенде: токен сервиса не
должен попадать в браузер ни при каких условиях.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_permission
from app.models import RecruitmentCandidate, User
from app.services import recruitment_pipeline
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


@router.patch("/pipeline/{row_id}")
async def update_pipeline_candidate(
    row_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("recruitment.view")),
):
    """Этап, ответственный, заметка."""
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
    await db.delete(row)
    await audit(
        db, current, "recruitment.candidate_removed",
        f"Кандидат «{title}» убран с доски найма",
        request=request, entity_id=str(row_id),
    )
    await db.commit()
    return {"deleted": str(row_id)}
