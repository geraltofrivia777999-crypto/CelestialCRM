"""Задачи — канбан-доска команды (ТЗ 8.1).

Доска делится на разделы — по отделу на раздел («Дизайнеры», «Баеры»). Раздел
хранит только свои карточки и свои права; колонки, пользовательские поля и
шаблоны общие на воркспейс, поэтому все разделы устроены одинаково.

Читать раздел без правил может любой с `workspace.view`: задачи ставятся друг
другу, и скрывать их внутри отдела смысла нет. Настройка колонок,
пользовательских полей, шаблонов и самих разделов — за `workspace.manage`: это
структура доски, а не отдельная карточка.

Порядок карточек хранится явным `position`, а не временем создания: доску
переставляют мышью, и без собственного порядка карточки прыгали бы после каждого
обновления страницы.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import has_full_access, has_permission, require_permission
from app.models import (
    KnowledgeAttachment,
    Role,
    Task,
    TaskAssignee,
    TaskField,
    TaskPriority,
    TaskSection,
    TaskSectionAccess,
    TaskStatus,
    TaskTemplate,
    User,
)
from app.schemas import (
    OPTION_FIELD_KINDS,
    Page,
    TaskCreate,
    TaskFieldCreate,
    TaskFieldUpdate,
    TaskMove,
    TaskSectionAccessUpdate,
    TaskSectionCreate,
    TaskSectionUpdate,
    TaskStatusCreate,
    TaskStatusUpdate,
    TaskTemplateCreate,
    TaskTemplateUpdate,
    TaskUpdate,
    normalize_field_config,
)
from app.services import attachments, storage, task_sections
from app.services.audit import audit
from app.services.task_sections import DEFAULT_ACCESS, FULL_ACCESS

router = APIRouter(prefix="/workspace", tags=["workspace"])

PRIORITY_ORDER = {
    TaskPriority.critical: 0,
    TaskPriority.high: 1,
    TaskPriority.medium: 2,
    TaskPriority.low: 3,
}
POSITION_STEP = 100
# Сколько вариантов может выбрать поле «Метки» в одной карточке. Ограничение
# нужно только чтобы карточка оставалась карточкой, а не списком тегов.
MAX_LABELS = 20
# Разделов на доске столько же, сколько отделов в команде: ограничение нужно
# только чтобы полоса вкладок оставалась полосой вкладок.
MAX_SECTIONS = 30
ATTACHMENT_URL = "/api/v1/workspace/attachments"


@router.get("/board")
async def board(
    section_id: uuid.UUID | None = None,
    assignee_id: uuid.UUID | None = None,
    status_id: uuid.UUID | None = None,
    priority: TaskPriority | None = None,
    search: str | None = None,
    sort: str = "position",
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.view")),
) -> dict:
    """Доска целиком: разделы, колонки, карточки и всё, что нужно формам.

    Фильтры применяются к карточкам, но не к колонкам: пустая колонка обязана
    остаться на доске, иначе в неё нельзя перетащить задачу.

    Карточки отдаются одного раздела — того, что попросили, или первого
    доступного. Закрытый раздел не подставляется молча: клиент помнит последний
    открытый раздел, и после закрытия доступа он должен получить отказ, а не
    чужую доску.
    """
    manager = await _is_manager(db, current)
    rights = await task_sections.section_rights(db, current, is_admin=manager)
    visible = [
        section
        for section in await task_sections.sections(db, current.workspace_id)
        if rights.get(section.id, DEFAULT_ACCESS)["can_view"]
    ]
    counts = await _section_counts(db, current.workspace_id)
    sections = [
        {
            "id": str(section.id),
            "title": section.title,
            "position": section.position,
            "tasks": counts.get(section.id, 0),
            "rights": rights.get(section.id, DEFAULT_ACCESS),
        }
        for section in visible
    ]
    active = _pick_section(visible, section_id)
    if active is None:
        # Ни одного доступного раздела: доска пустая, но интерфейс обязан
        # получить ответ, а не ошибку, — иначе не показать «попросите доступ».
        return {
            "sections": sections,
            "section_id": None,
            "columns": [],
            "fields": [_field_row(field) for field in await _fields(db, current.workspace_id)],
            "attachments": {},
            "total": 0,
            "overdue": 0,
            "sort": sort,
            "can_manage_sections": manager,
        }

    statuses = await _statuses(db, current.workspace_id)
    fields = await _fields(db, current.workspace_id)

    filters = [Task.workspace_id == current.workspace_id, Task.section_id == active.id]
    if status_id:
        filters.append(Task.status_id == status_id)
    if priority:
        filters.append(Task.priority == priority)
    # SQLite's LOWER/LIKE do not fold non-ASCII letters, while PostgreSQL's
    # behaviour depends on the database collation.  Filtering the (already
    # workspace-scoped) result with Python's Unicode-aware casefold keeps a
    # Cyrillic title search identical in development, tests and production.
    search_text = search.strip().casefold() if search else ""
    if assignee_id:
        filters.append(
            Task.id.in_(
                select(TaskAssignee.task_id).where(TaskAssignee.user_id == assignee_id)
            )
        )

    tasks = list((await db.execute(select(Task).where(*filters))).scalars())
    if search_text:
        tasks = [task for task in tasks if search_text in task.title.casefold()]
    names = await _user_names(db, current.workspace_id)
    templates = {row.id: row for row in await _templates(db, current.workspace_id)}
    rows = [_task_row(task, names, current, fields, templates, rights) for task in tasks]
    _sort_rows(rows, sort)

    today = datetime.now(UTC).date()
    columns = []
    for status in statuses:
        column = [row for row in rows if row["status_id"] == str(status.id)]
        columns.append(
            {
                "id": str(status.id),
                "code": status.code,
                "name": status.name,
                "color": status.color,
                "is_system": status.is_system,
                "is_terminal": status.is_terminal,
                "tasks": column,
                "count": len(column),
            }
        )

    return {
        "sections": sections,
        "section_id": str(active.id),
        "can_manage_sections": manager,
        "columns": columns,
        "fields": [_field_row(field) for field in fields],
        # Метаданные файлов, на которые ссылаются поля типа «Файлы». В самих
        # значениях лежат только ID: так карточку можно сохранить обратно, ничего
        # не пересобирая, а имя и размер файла всё равно нужны для отрисовки.
        "attachments": await _task_attachment_rows(db, current.workspace_id),
        "total": len(rows),
        "overdue": sum(
            1
            for row in rows
            if row["due_date"] and row["due_date"] < today.isoformat() and not row["is_done"]
        ),
        "sort": sort,
    }


@router.post("/tasks", status_code=201)
async def create_task(
    payload: TaskCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.view")),
) -> dict:
    statuses = await _statuses(db, current.workspace_id)
    if not statuses:
        raise HTTPException(status_code=422, detail="На доске нет ни одной колонки")

    values = payload.model_dump()
    rights = await _rights(db, current)
    section = await _section_for_create(db, current, values.pop("section_id", None), rights)
    template_id = values.pop("template_id", None)
    template = await _template(db, current, template_id) if template_id else None
    if template is not None:
        values = _apply_template(template, values, payload.model_fields_set)

    status = _pick_status(statuses, values.get("status_id"))
    fields = await _fields(db, current.workspace_id)
    raw_values = values.get("custom_values") or {}
    visible = _visible_fields(fields, template, raw_values)
    custom_values = _clean_custom_values(raw_values, visible)
    assignees = await _valid_assignees(db, current, values.get("assignee_ids") or [])
    title = _required_text(values.get("title"), "Название задачи")
    _check_task_dates(values.get("start_date"), values.get("due_date"))

    task = Task(
        workspace_id=current.workspace_id,
        section_id=section.id,
        status_id=status.id,
        title=title,
        description=values.get("description"),
        start_date=values.get("start_date"),
        due_date=values.get("due_date"),
        priority=values.get("priority") or TaskPriority.medium,
        custom_values=custom_values,
        template_id=template.id if template is not None else None,
        created_by_id=current.id,
        position=await _next_position(db, current.workspace_id, section.id, status.id),
        completed_at=datetime.now(UTC) if status.is_terminal else None,
    )
    task.assignees = [TaskAssignee(user_id=user_id) for user_id in assignees]
    db.add(task)
    # Файл привязывается к задаче только когда у задачи появился ID, поэтому
    # flush до привязки обязателен.
    await db.flush()
    await _bind_task_attachments(db, current, task, custom_values, visible)
    await audit(
        db, current, "workspace.task_created", f"Создана задача «{task.title}»", request=request
    )
    await db.commit()
    await db.refresh(task)
    return _task_row(
        task,
        await _user_names(db, current.workspace_id),
        current,
        await _fields(db, current.workspace_id),
        {row.id: row for row in await _templates(db, current.workspace_id)},
        rights,
    )


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.view")),
) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    rights = await _rights(db, current)
    probe = await _task(db, current, task_id, rights=rights)
    _assert_task_editable(probe, current, rights)
    dropped: list[str] = []
    if "title" in changes:
        changes["title"] = _required_text(changes["title"], "Название задачи")

    target_status = None
    if changes.get("status_id"):
        target_status = _pick_status(
            await _statuses(db, current.workspace_id), changes["status_id"]
        )
    # Раздел проверяем до блокировки: перенос в чужой раздел — это создание там
    # карточки, поэтому нужно право «создание», а не только правка исходной.
    target_section = None
    if changes.get("section_id"):
        target_section = await _section_for_create(
            db, current, changes["section_id"], rights
        )

    task = await _editable_task(
        db,
        current,
        task_id,
        rights=rights,
        target_status_id=target_status.id if target_status else None,
    )

    if target_section is not None and target_section.id != task.section_id:
        task.section_id = target_section.id
        task.position = await _next_position(
            db, current.workspace_id, target_section.id, task.status_id
        )
    changes.pop("section_id", None)

    if "status_id" in changes and changes["status_id"]:
        status = target_status
        assert status is not None
        if status.id != task.status_id:
            next_position = await _next_position(
                db, current.workspace_id, task.section_id, status.id
            )
            task.status_id = status.id
            task.position = next_position
            task.completed_at = datetime.now(UTC) if status.is_terminal else None
    if "assignee_ids" in changes:
        assignees = await _valid_assignees(db, current, changes.pop("assignee_ids") or [])
        # Через коллекцию, а не отдельным DELETE: cascade delete-orphan сам уберёт
        # прежних исполнителей, и сессия не останется с устаревшим списком.
        task.assignees = [TaskAssignee(user_id=user_id) for user_id in assignees]
    if "template_id" in changes:
        template_id = changes.pop("template_id")
        task.template_id = (
            (await _template(db, current, template_id)).id if template_id else None
        )
    if "custom_values" in changes:
        raw_values = changes.pop("custom_values") or {}
        fields = await _fields(db, current.workspace_id)
        template = (
            await _template(db, current, task.template_id) if task.template_id else None
        )
        visible = _visible_fields(fields, template, raw_values)
        task.custom_values = _clean_custom_values(raw_values, visible)
        dropped = await _bind_task_attachments(db, current, task, task.custom_values, visible)
    for field in ("title", "description", "start_date", "due_date", "priority"):
        if field in changes:
            value = changes[field]
            setattr(task, field, value.strip() if field == "title" and value else value)
    _check_task_dates(task.start_date, task.due_date)

    await audit(
        db, current, "workspace.task_updated", f"Изменена задача «{task.title}»",
        request=request, entity_id=str(task.id),
    )
    await db.commit()
    # Байты стираем только после успешной транзакции: если коммит не прошёл,
    # значение поля осталось прежним и файл ещё нужен.
    for path in dropped:
        storage.remove(path)
    await db.refresh(task)
    return _task_row(
        task,
        await _user_names(db, current.workspace_id),
        current,
        await _fields(db, current.workspace_id),
        {row.id: row for row in await _templates(db, current.workspace_id)},
        rights,
    )


@router.post("/tasks/{task_id}/move")
async def move_task(
    task_id: uuid.UUID,
    payload: TaskMove,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.view")),
) -> dict:
    """Перетаскивание карточки между колонками — ТЗ 8.1.

    Позиции внутри колонки пересчитываются целиком с шагом: вставка «между»
    двумя соседями иначе требует дробных значений, а они рано или поздно
    упираются в точность.
    """
    rights = await _rights(db, current)
    probe = await _task(db, current, task_id, rights=rights)
    _assert_task_editable(probe, current, rights)
    status = _pick_status(await _statuses(db, current.workspace_id), payload.status_id)
    task = await _editable_task(
        db, current, task_id, rights=rights, target_status_id=status.id
    )

    siblings = list(
        (
            await db.execute(
                select(Task)
                .where(
                    Task.workspace_id == current.workspace_id,
                    # Порядок считается внутри своего раздела: у каждого отдела
                    # своя доска, и чужие карточки в его колонках не участвуют.
                    Task.section_id == task.section_id,
                    Task.status_id == status.id,
                    Task.id != task.id,
                )
                .order_by(Task.position, Task.created_at)
                .with_for_update()
            )
        ).scalars()
    )
    index = max(0, min(payload.position, len(siblings)))
    siblings.insert(index, task)

    moved_column = task.status_id != status.id
    task.status_id = status.id
    if moved_column:
        task.completed_at = datetime.now(UTC) if status.is_terminal else None
    for order, item in enumerate(siblings):
        item.position = order * POSITION_STEP

    await db.commit()
    return {"id": str(task.id), "status_id": str(status.id), "position": index}


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.view")),
) -> dict:
    rights = await _rights(db, current)
    task = await _editable_task(db, current, task_id, rights=rights, mode="delete")
    title = task.title
    # Вложения удаляем явно, а не каскадом БД: каскад описан на уровне схемы и
    # зависит от того, включены ли внешние ключи, а байты с диска он всё равно
    # не уберёт — файл навсегда остался бы в квоте воркспейса.
    dropped: list[str] = []
    for attachment in (
        await db.execute(
            select(KnowledgeAttachment).where(KnowledgeAttachment.task_id == task.id)
        )
    ).scalars():
        dropped.append(attachment.storage_path)
        await db.delete(attachment)
    await db.flush()
    await db.delete(task)
    await audit(
        db, current, "workspace.task_deleted", f"Удалена задача «{title}»",
        request=request, entity_id=str(task_id),
    )
    await db.commit()
    for path in dropped:
        storage.remove(path)
    return {"deleted": str(task_id)}


# --- разделы доски -----------------------------------------------------------


@router.post("/sections", status_code=201)
async def create_section(
    payload: TaskSectionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    title = _required_text(payload.title, "Название раздела")
    existing = await task_sections.sections(db, current.workspace_id)
    if any(row.title.casefold() == title.casefold() for row in existing):
        raise HTTPException(status_code=422, detail="Раздел с таким названием уже есть")
    if len(existing) >= MAX_SECTIONS:
        raise HTTPException(
            status_code=422, detail=f"Больше {MAX_SECTIONS} разделов доска не выдержит"
        )
    section = TaskSection(
        workspace_id=current.workspace_id,
        title=title,
        position=payload.position if payload.position is not None else len(existing),
        created_by_id=current.id,
    )
    db.add(section)
    await audit(
        db, current, "workspace.section_created", f"Создан раздел задач «{title}»",
        request=request,
    )
    await db.commit()
    await db.refresh(section)
    return {
        "id": str(section.id),
        "title": section.title,
        "position": section.position,
        "tasks": 0,
        "rights": dict(FULL_ACCESS),
    }


@router.patch("/sections/{section_id}")
async def update_section(
    section_id: uuid.UUID,
    payload: TaskSectionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    section = await _section(db, current, section_id)
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes and changes["title"] is not None:
        title = _required_text(changes["title"], "Название раздела")
        taken = [
            row
            for row in await task_sections.sections(db, current.workspace_id)
            if row.id != section.id and row.title.casefold() == title.casefold()
        ]
        if taken:
            raise HTTPException(status_code=422, detail="Раздел с таким названием уже есть")
        section.title = title
    if changes.get("position") is not None:
        section.position = changes["position"]
    await audit(
        db, current, "workspace.section_updated", f"Изменён раздел задач «{section.title}»",
        request=request, entity_id=str(section.id),
    )
    await db.commit()
    return {"id": str(section.id), "title": section.title, "position": section.position}


@router.delete("/sections/{section_id}")
async def delete_section(
    section_id: uuid.UUID,
    request: Request,
    move_to: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    """Удалить раздел, перенеся задачи в другой.

    Задачи вместе с разделом не удаляются: их либо переносят, либо удаление не
    проходит. Последний раздел удалить нельзя — карточке некуда было бы лечь.
    """
    section = await _section(db, current, section_id)
    if len(await task_sections.sections(db, current.workspace_id)) <= 1:
        raise HTTPException(
            status_code=422,
            detail="Это единственный раздел доски — сначала создайте другой",
        )
    tasks = await db.scalar(
        select(func.count()).select_from(Task).where(Task.section_id == section.id)
    )
    if tasks:
        if not move_to:
            raise HTTPException(
                status_code=422,
                detail=f"В разделе {tasks} задач — укажите, куда их перенести",
            )
        target = await _section(db, current, move_to)
        if target.id == section.id:
            raise HTTPException(status_code=422, detail="Некуда переносить задачи")
        rows = list(
            (await db.execute(select(Task).where(Task.section_id == section.id))).scalars()
        )
        # Позиции считаются по колонкам: в целевом разделе своя очередь в
        # каждой из них, и переносимые карточки встают в её конец.
        base: dict[uuid.UUID, int] = {}
        for task in rows:
            if task.status_id not in base:
                base[task.status_id] = await _next_position(
                    db, current.workspace_id, target.id, task.status_id
                )
            task.section_id = target.id
            task.position = base[task.status_id]
            base[task.status_id] += POSITION_STEP
        await db.flush()

    title = section.title
    await db.delete(section)
    await audit(
        db, current, "workspace.section_deleted", f"Удалён раздел задач «{title}»",
        request=request, entity_id=str(section_id),
    )
    await db.commit()
    return {"deleted": str(section_id), "moved": tasks or 0}


@router.get("/sections/{section_id}/access")
async def section_access(
    section_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    section = await _section(db, current, section_id)
    roles = list(
        (
            await db.execute(
                select(Role)
                .where(Role.workspace_id == current.workspace_id)
                .order_by(Role.name)
            )
        ).scalars()
    )
    people = list(
        (
            await db.execute(
                select(User)
                .where(User.workspace_id == current.workspace_id)
                .order_by(User.name, User.login)
            )
        ).scalars()
    )
    existing = list(
        (
            await db.execute(
                select(TaskSectionAccess).where(TaskSectionAccess.section_id == section.id)
            )
        ).scalars()
    )
    by_role = {row.role_id: row for row in existing if row.role_id is not None}
    by_user = {row.user_id: row for row in existing if row.user_id is not None}

    def entry(rule: TaskSectionAccess | None) -> dict:
        if rule is None:
            return {"configured": False, **DEFAULT_ACCESS}
        return {
            "configured": True,
            "can_view": rule.can_view,
            "can_create": rule.can_create,
            "can_edit": rule.can_edit,
            "can_delete": rule.can_delete,
            "can_manage": rule.can_manage,
        }

    return {
        "section_id": str(section.id),
        "title": section.title,
        "open": not existing,
        "roles": [
            {"role_id": str(role.id), "role_name": role.name, **entry(by_role.get(role.id))}
            for role in roles
        ],
        "users": [
            {
                "user_id": str(person.id),
                "user_name": person.name or person.login,
                **entry(by_user.get(person.id)),
            }
            for person in people
        ],
    }


@router.put("/sections/{section_id}/access")
async def set_section_access(
    section_id: uuid.UUID,
    payload: TaskSectionAccessUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    """Заменить правила доступа к разделу доски.

    Пустой список возвращает раздел в открытое состояние, а не закрывает его
    всем: закрытие должно быть явным — им отмечают тех, кому раздел нужен.
    """
    section = await _section(db, current, section_id)
    role_ids = set(
        (
            await db.execute(select(Role.id).where(Role.workspace_id == current.workspace_id))
        ).scalars()
    )
    user_ids = set(
        (
            await db.execute(select(User.id).where(User.workspace_id == current.workspace_id))
        ).scalars()
    )
    seen: set[tuple[str, uuid.UUID]] = set()
    for rule in payload.rules:
        if rule.role_id is not None and rule.role_id not in role_ids:
            raise HTTPException(status_code=422, detail="Такой роли в воркспейсе нет")
        if rule.user_id is not None and rule.user_id not in user_ids:
            raise HTTPException(status_code=422, detail="Такого пользователя в воркспейсе нет")
        key = ("role", rule.role_id) if rule.role_id else ("user", rule.user_id)
        if key in seen:
            raise HTTPException(status_code=422, detail="Для одного адресата задано два правила")
        seen.add(key)

    existing = list(
        (
            await db.execute(
                select(TaskSectionAccess).where(TaskSectionAccess.section_id == section.id)
            )
        ).scalars()
    )
    for row in existing:
        await db.delete(row)
    await db.flush()
    for rule in payload.rules:
        db.add(
            TaskSectionAccess(
                workspace_id=current.workspace_id,
                section_id=section.id,
                role_id=rule.role_id,
                user_id=rule.user_id,
                can_view=rule.can_view,
                can_create=rule.can_create,
                can_edit=rule.can_edit,
                can_delete=rule.can_delete,
                can_manage=rule.can_manage,
            )
        )
    await audit(
        db, current, "workspace.section_access_updated",
        f"Изменён доступ к разделу задач «{section.title}»: правил {len(payload.rules)}",
        request=request, entity_id=str(section.id),
    )
    await db.commit()
    return {"section_id": str(section.id), "rules": len(payload.rules)}


# --- колонки доски -----------------------------------------------------------


@router.get("/statuses", response_model=Page)
async def list_statuses(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.view")),
) -> Page:
    rows = await _statuses(db, current.workspace_id)
    counts = await _status_counts(db, current.workspace_id)
    items = [
        {
            "id": str(row.id),
            "code": row.code,
            "name": row.name,
            "color": row.color,
            "position": row.position,
            "is_system": row.is_system,
            "is_terminal": row.is_terminal,
            "tasks": counts.get(row.id, 0),
        }
        for row in rows
    ]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@router.post("/statuses", status_code=201)
async def create_status(
    payload: TaskStatusCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    name = _required_text(payload.name, "Название колонки")
    existing = await _statuses(db, current.workspace_id)
    if any(row.name.casefold() == name.casefold() for row in existing):
        raise HTTPException(status_code=422, detail="Колонка с таким названием уже есть")
    if len(existing) >= 20:
        raise HTTPException(status_code=422, detail="Больше 20 колонок доска не выдержит")
    status = TaskStatus(
        workspace_id=current.workspace_id,
        code=_status_code(name, {row.code for row in existing}),
        name=name,
        color=payload.color,
        is_terminal=payload.is_terminal,
        position=payload.position if payload.position is not None else len(existing),
    )
    db.add(status)
    await audit(
        db, current, "workspace.status_created", f"Добавлена колонка «{status.name}»",
        request=request,
    )
    await db.commit()
    await db.refresh(status)
    return {"id": str(status.id), "code": status.code, "name": status.name}


@router.patch("/statuses/{status_id}")
async def update_status(
    status_id: uuid.UUID,
    payload: TaskStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    status = await db.get(TaskStatus, status_id)
    if not status or status.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Колонка не найдена")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = _required_text(changes["name"], "Название колонки")
    for field, value in changes.items():
        if value is not None:
            setattr(status, field, value)
    await audit(
        db, current, "workspace.status_updated", f"Изменена колонка «{status.name}»",
        request=request, entity_id=str(status.id),
    )
    await db.commit()
    return {"id": str(status.id), "name": status.name}


@router.delete("/statuses/{status_id}")
async def delete_status(
    status_id: uuid.UUID,
    request: Request,
    move_to: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    """Удалить колонку, перенеся задачи в другую.

    Базовые пять колонок не удаляются: на них завязаны отчёты и смысл доски.
    Задачи никогда не удаляются вместе с колонкой — их либо переносят, либо
    удаление не проходит.
    """
    status = await db.get(TaskStatus, status_id)
    if not status or status.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Колонка не найдена")
    if status.is_system:
        raise HTTPException(
            status_code=422, detail="Базовые колонки удалить нельзя — их можно переименовать"
        )
    tasks = await db.scalar(
        select(func.count()).select_from(Task).where(Task.status_id == status.id)
    )
    if tasks:
        if not move_to:
            raise HTTPException(
                status_code=422,
                detail=f"В колонке {tasks} задач — укажите, куда их перенести",
            )
        target = await db.get(TaskStatus, move_to)
        if not target or target.workspace_id != current.workspace_id or target.id == status.id:
            raise HTTPException(status_code=422, detail="Некуда переносить задачи")
        rows = list(
            (await db.execute(select(Task).where(Task.status_id == status.id))).scalars()
        )
        # Колонка общая на все разделы, а очередь в ней у каждого своя, поэтому
        # позиции считаются по разделу переносимой карточки.
        base: dict[uuid.UUID, int] = {}
        for task in rows:
            if task.section_id not in base:
                base[task.section_id] = await _next_position(
                    db, current.workspace_id, task.section_id, target.id
                )
            task.status_id = target.id
            task.position = base[task.section_id]
            base[task.section_id] += POSITION_STEP
        await db.flush()

    name = status.name
    await db.delete(status)
    await audit(
        db, current, "workspace.status_deleted", f"Удалена колонка «{name}»",
        request=request, entity_id=str(status_id),
    )
    await db.commit()
    return {"deleted": str(status_id), "moved": tasks or 0}


# --- пользовательские поля ---------------------------------------------------


@router.post("/fields", status_code=201)
async def create_field(
    payload: TaskFieldCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    name = _required_text(payload.name, "Название поля")
    existing = await _fields(db, current.workspace_id)
    if any(row.name.casefold() == name.casefold() for row in existing):
        raise HTTPException(status_code=422, detail="Поле с таким названием уже есть")
    field = TaskField(
        workspace_id=current.workspace_id,
        name=name,
        kind=payload.kind,
        options=payload.options,
        config=payload.config,
        is_required=payload.is_required,
        show_always=payload.show_always,
        position=payload.position if payload.position is not None else len(existing),
    )
    db.add(field)
    await audit(
        db, current, "workspace.field_created", f"Добавлено поле задачи «{field.name}»",
        request=request,
    )
    await db.commit()
    await db.refresh(field)
    return _field_row(field)


@router.patch("/fields/{field_id}")
async def update_field(
    field_id: uuid.UUID,
    payload: TaskFieldUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    field = await db.get(TaskField, field_id)
    if not field or field.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Поле не найдено")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = _required_text(changes["name"], "Название поля")
    # Тип поля не меняется: уже заполненные значения после смены типа стали бы
    # мусором, а молча их терять нельзя.
    if "options" in changes and field.kind not in OPTION_FIELD_KINDS:
        changes.pop("options")
    if "config" in changes and changes["config"] is not None:
        try:
            changes["config"] = normalize_field_config(field.kind, changes["config"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    for name, value in changes.items():
        if value is not None:
            setattr(field, name, value)
    await audit(
        db, current, "workspace.field_updated", f"Изменено поле задачи «{field.name}»",
        request=request, entity_id=str(field.id),
    )
    await db.commit()
    return _field_row(field)


@router.delete("/fields/{field_id}")
async def delete_field(
    field_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    field = await db.get(TaskField, field_id)
    if not field or field.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Поле не найдено")
    name = field.name
    key = str(field.id)
    kind = field.kind
    await db.delete(field)
    # Значения удалённого поля вычищаем из карточек: иначе они остаются в JSON
    # навсегда и всплывают, если завести поле с тем же ID.
    tasks = list(
        (
            await db.execute(
                select(Task).where(Task.workspace_id == current.workspace_id)
            )
        ).scalars()
    )
    orphan_ids: set[uuid.UUID] = set()
    for task in tasks:
        if key in (task.custom_values or {}):
            values = dict(task.custom_values)
            value = values.pop(key, None)
            task.custom_values = values
            if kind == "file":
                for item in value if isinstance(value, list) else [value]:
                    try:
                        orphan_ids.add(uuid.UUID(str(item)))
                    except (TypeError, ValueError):
                        continue

    # Файлы удалённого поля больше ниоткуда не видны: без явной уборки они
    # навсегда остались бы в квоте воркспейса.
    dropped: list[str] = []
    if orphan_ids:
        for attachment in (
            await db.execute(
                select(KnowledgeAttachment).where(
                    KnowledgeAttachment.workspace_id == current.workspace_id,
                    KnowledgeAttachment.id.in_(orphan_ids),
                )
            )
        ).scalars():
            dropped.append(attachment.storage_path)
            await db.delete(attachment)

    # Из шаблонов поле тоже убираем: иначе шаблон ссылался бы на несуществующее
    # поле и падал бы при следующем сохранении.
    for template in await _templates(db, current.workspace_id):
        if key in [str(value) for value in (template.field_ids or [])]:
            template.field_ids = [
                str(value) for value in template.field_ids if str(value) != key
            ]
        if key in (template.custom_values or {}):
            defaults = dict(template.custom_values)
            defaults.pop(key, None)
            template.custom_values = defaults

    await audit(
        db, current, "workspace.field_deleted", f"Удалено поле задачи «{name}»",
        request=request, entity_id=str(field_id),
    )
    await db.commit()
    for path in dropped:
        storage.remove(path)
    return {"deleted": str(field_id)}


# --- файлы полей типа «Файлы» ------------------------------------------------


@router.post("/attachments", status_code=201)
async def upload_task_attachment(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.view")),
) -> dict:
    """Загрузить файл для поля задачи.

    Файл сначала висит ничей и закрепляется за задачей при сохранении карточки:
    форму могут закрыть, не сохранив, а вернуться к незагруженному полю нельзя —
    браузер второй раз тот же файл сам не отдаст.
    """
    attachment, stale = await attachments.receive_upload(db, current, file)
    path = attachment.storage_path
    try:
        await audit(
            db, current, "workspace.attachment_uploaded",
            f"Загружен файл задачи «{attachment.file_name}»", request=request,
        )
        await db.commit()
        await db.refresh(attachment)
    except Exception:
        await db.rollback()
        storage.remove(path)
        raise
    for row in stale:
        storage.remove(row.storage_path)
    return attachments.row(attachment, ATTACHMENT_URL)


@router.get("/attachments/{attachment_id}")
async def read_task_attachment(
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.view")),
) -> Response:
    """Отдать файл задачи.

    Через API, а не напрямую с диска: доска доступна только своей команде, а
    прямая ссылка на файл обошла бы и это, и проверку сессии.
    """
    attachment = await db.get(KnowledgeAttachment, attachment_id)
    if (
        not attachment
        or attachment.workspace_id != current.workspace_id
        or attachment.article_id is not None
    ):
        raise HTTPException(status_code=404, detail="Файл не найден")
    if attachment.task_id is None and attachment.uploaded_by_id != current.id:
        # Ещё не закреплённый файл виден только тому, кто его загрузил: пока
        # он не в карточке, «доступ есть у всей команды» ещё не наступил.
        if not has_permission(current, "workspace.manage"):
            raise HTTPException(status_code=404, detail="Файл не найден")
    try:
        path = storage.resolve(attachment.storage_path)
    except storage.StorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=path.read_bytes(),
        media_type=attachment.mime_type,
        headers={
            # nosniff обязателен: файл, который браузер решит считать HTML,
            # выполнит скрипты в нашем домене.
            "Content-Disposition": f'inline; filename="{attachment.id}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


# --- шаблоны задач -----------------------------------------------------------


@router.get("/task-templates", response_model=Page)
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.view")),
) -> Page:
    rows = list(
        (
            await db.execute(
                select(TaskTemplate)
                .where(TaskTemplate.workspace_id == current.workspace_id)
                .order_by(TaskTemplate.name)
            )
        ).scalars()
    )
    items = [_template_row(row) for row in rows]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@router.post("/task-templates", status_code=201)
async def create_template(
    payload: TaskTemplateCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    name = _required_text(payload.name, "Название шаблона")
    duplicate = await db.scalar(
        select(TaskTemplate).where(
            TaskTemplate.workspace_id == current.workspace_id,
            TaskTemplate.name == name,
        )
    )
    if duplicate:
        raise HTTPException(status_code=422, detail="Шаблон с таким названием уже есть")
    fields = await _fields(db, current.workspace_id)
    values = payload.model_dump()
    values.update(
        {
            "name": name,
            "custom_values": _clean_custom_values(
                payload.custom_values, fields, allow_files=False, require=False
            ),
            "field_ids": _valid_field_ids(payload.field_ids, fields),
        }
    )
    template = TaskTemplate(
        workspace_id=current.workspace_id,
        created_by_id=current.id,
        **values,
    )
    db.add(template)
    await db.flush()
    await _apply_default(db, current.workspace_id, template)
    await audit(
        db, current, "workspace.task_template_created",
        f"Создан шаблон задачи «{template.name}»", request=request,
    )
    await db.commit()
    await db.refresh(template)
    return _template_row(template)


async def _apply_default(
    db: AsyncSession, workspace_id: uuid.UUID, template: TaskTemplate
) -> None:
    """Шаблон по умолчанию — ровно один на воркспейс.

    Снимаем отметку с остальных прямо здесь, а не проверяем при чтении: два
    шаблона «по умолчанию» означали бы, что новая задача открывается то с
    одним брифом, то с другим.
    """
    if not template.is_default:
        return
    await db.execute(
        update(TaskTemplate)
        .where(
            TaskTemplate.workspace_id == workspace_id,
            TaskTemplate.id != template.id,
        )
        .values(is_default=False)
    )


@router.patch("/task-templates/{template_id}")
async def update_template(
    template_id: uuid.UUID,
    payload: TaskTemplateUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    template = await _template(db, current, template_id)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = _required_text(changes["name"], "Название шаблона")
    if "custom_values" in changes or "field_ids" in changes:
        fields = await _fields(db, current.workspace_id)
        if "custom_values" in changes:
            changes["custom_values"] = _clean_custom_values(
                changes["custom_values"] or {}, fields, allow_files=False, require=False
            )
        if "field_ids" in changes:
            changes["field_ids"] = _valid_field_ids(changes["field_ids"] or [], fields)
    for field, value in changes.items():
        setattr(template, field, value)
    await _apply_default(db, current.workspace_id, template)
    await audit(
        db, current, "workspace.task_template_updated",
        f"Изменён шаблон задачи «{template.name}»", request=request, entity_id=str(template.id),
    )
    await db.commit()
    await db.refresh(template)
    return _template_row(template)


@router.delete("/task-templates/{template_id}")
async def delete_template(
    template_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("workspace.manage")),
) -> dict:
    template = await _template(db, current, template_id)
    name = template.name
    await db.delete(template)
    await audit(
        db, current, "workspace.task_template_deleted", f"Удалён шаблон задачи «{name}»",
        request=request, entity_id=str(template_id),
    )
    await db.commit()
    return {"deleted": str(template_id)}


async def _statuses(db: AsyncSession, workspace_id: uuid.UUID) -> list[TaskStatus]:
    return list(
        (
            await db.execute(
                select(TaskStatus)
                .where(TaskStatus.workspace_id == workspace_id)
                .order_by(TaskStatus.position, TaskStatus.created_at)
            )
        ).scalars()
    )


async def _status_counts(db: AsyncSession, workspace_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = await db.execute(
        select(Task.status_id, func.count())
        .where(Task.workspace_id == workspace_id)
        .group_by(Task.status_id)
    )
    return {status_id: count for status_id, count in rows}


async def _templates(db: AsyncSession, workspace_id: uuid.UUID) -> list[TaskTemplate]:
    return list(
        (
            await db.execute(
                select(TaskTemplate)
                .where(TaskTemplate.workspace_id == workspace_id)
                .order_by(TaskTemplate.name)
            )
        ).scalars()
    )


async def _task_attachment_rows(db: AsyncSession, workspace_id: uuid.UUID) -> dict:
    rows = list(
        (
            await db.execute(
                select(KnowledgeAttachment).where(
                    KnowledgeAttachment.workspace_id == workspace_id,
                    KnowledgeAttachment.task_id.is_not(None),
                )
            )
        ).scalars()
    )
    return {str(row.id): attachments.row(row, ATTACHMENT_URL) for row in rows}


async def _bind_task_attachments(
    db: AsyncSession,
    current: User,
    task: Task,
    values: dict,
    fields: list[TaskField],
) -> list[str]:
    """Закрепить файлы за задачей и вернуть пути тех, что из неё убрали.

    Пока файл не закреплён, фоновая уборка считает его брошенным и через сутки
    удаляет. Убранный из поля файл удаляется сразу: держать его дальше незачем —
    ссылки на него больше нет нигде, а место в квоте он занимает.
    """
    file_field_ids = {str(field.id) for field in fields if field.kind == "file"}
    wanted: set[uuid.UUID] = set()
    for key, value in (values or {}).items():
        if key not in file_field_ids:
            continue
        for item in value if isinstance(value, list) else [value]:
            wanted.add(uuid.UUID(str(item)))

    rows = list(
        (
            await db.execute(
                select(KnowledgeAttachment).where(
                    KnowledgeAttachment.workspace_id == current.workspace_id,
                    (KnowledgeAttachment.task_id == task.id)
                    | (
                        KnowledgeAttachment.id.in_(wanted)
                        if wanted
                        else KnowledgeAttachment.id.is_(None)
                    ),
                )
            )
        ).scalars()
    )
    by_id = {row.id: row for row in rows}
    privileged = has_permission(current, "workspace.manage")
    for attachment_id in wanted:
        attachment = by_id.get(attachment_id)
        if (
            attachment is None
            or attachment.article_id is not None
            or (attachment.task_id is not None and attachment.task_id != task.id)
            or (
                attachment.task_id is None
                and attachment.uploaded_by_id != current.id
                and not privileged
            )
        ):
            # Не уточняем, чужой это воркспейс, чужая задача или статья: по
            # ответу не должно быть видно, существует ли такой файл вообще.
            raise HTTPException(
                status_code=422,
                detail="Один из файлов недоступен или уже используется в другой задаче",
            )

    removed: list[str] = []
    for attachment in rows:
        if attachment.id in wanted:
            attachment.task_id = task.id
        elif attachment.task_id == task.id:
            removed.append(attachment.storage_path)
            await db.delete(attachment)
    return removed


async def _fields(db: AsyncSession, workspace_id: uuid.UUID) -> list[TaskField]:
    return list(
        (
            await db.execute(
                select(TaskField)
                .where(TaskField.workspace_id == workspace_id)
                .order_by(TaskField.position, TaskField.created_at)
            )
        ).scalars()
    )


async def _is_manager(db: AsyncSession, current: User) -> bool:
    """Кто управляет доской, тот видит все её разделы.

    `workspace.manage` — это право на устройство доски целиком; закрывать от
    него разделы бессмысленно, он всё равно может открыть их себе сам.
    """
    return has_permission(current, "workspace.manage") or await has_full_access(db, current)


async def _rights(db: AsyncSession, current: User) -> dict[uuid.UUID, dict]:
    return await task_sections.section_rights(
        db, current, is_admin=await _is_manager(db, current)
    )


async def _section(db: AsyncSession, current: User, section_id: uuid.UUID) -> TaskSection:
    section = await db.get(TaskSection, section_id)
    if not section or section.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Раздел доски не найден")
    return section


def _pick_section(
    sections: list[TaskSection], section_id: uuid.UUID | None
) -> TaskSection | None:
    if section_id is None:
        return sections[0] if sections else None
    for section in sections:
        if section.id == section_id:
            return section
    raise HTTPException(status_code=403, detail="Раздел доски закрыт")


async def _section_for_create(
    db: AsyncSession,
    current: User,
    section_id: uuid.UUID | None,
    rights: dict[uuid.UUID, dict],
) -> TaskSection:
    """Раздел, в котором заводится карточка.

    Без явного раздела берётся первый, где человеку разрешено создавать: клиент
    старых версий и любой скрипт про разделы не знают, а задача должна лечь на
    доску, которая у автора действительно есть.
    """
    rows = await task_sections.sections(db, current.workspace_id)
    if section_id is not None:
        section = await _section(db, current, section_id)
        if not rights.get(section.id, DEFAULT_ACCESS)["can_create"]:
            raise HTTPException(
                status_code=403, detail="В этом разделе доски нельзя создавать задачи"
            )
        return section
    for section in rows:
        if rights.get(section.id, DEFAULT_ACCESS)["can_create"]:
            return section
    raise HTTPException(
        status_code=403, detail="Нет раздела доски, в котором вам разрешено заводить задачи"
    )


async def _section_counts(
    db: AsyncSession, workspace_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    rows = await db.execute(
        select(Task.section_id, func.count())
        .where(Task.workspace_id == workspace_id)
        .group_by(Task.section_id)
    )
    return {section_id: count for section_id, count in rows}


async def _task(
    db: AsyncSession,
    current: User,
    task_id: uuid.UUID,
    *,
    rights: dict[uuid.UUID, dict],
    for_update: bool = False,
) -> Task:
    statement = select(Task).where(
        Task.id == task_id,
        Task.workspace_id == current.workspace_id,
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    task = await db.scalar(statement)
    if not task or task.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    # Закрытый раздел отвечает «не найдена», а не «нет прав»: по коду ответа
    # иначе видно, что задача существует.
    if not rights.get(task.section_id, DEFAULT_ACCESS)["can_view"]:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task


async def _editable_task(
    db: AsyncSession,
    current: User,
    task_id: uuid.UUID,
    *,
    rights: dict[uuid.UUID, dict],
    target_status_id: uuid.UUID | None = None,
    mode: str = "edit",
) -> Task:
    """Return a task locked for mutation after checking card-level access.

    A status-row lock serializes every compliant reorder within a column.  It
    also prevents two concurrent creates/moves from calculating the same next
    position.  SQLite safely ignores ``FOR UPDATE``; PostgreSQL enforces it.
    """
    task = await _task(db, current, task_id, rights=rights)
    _assert_task_editable(task, current, rights, mode=mode)

    status_ids = {task.status_id}
    if target_status_id:
        status_ids.add(target_status_id)
    await _lock_statuses(db, current.workspace_id, status_ids)

    # Re-read under a row lock after waiting for the column lock: permissions
    # or the source column may have changed in a concurrent request.
    task = await _task(db, current, task_id, rights=rights, for_update=True)
    if task.status_id not in status_ids:
        await _lock_statuses(db, current.workspace_id, {task.status_id})
    _assert_task_editable(task, current, rights, mode=mode)
    return task


def _owns_task(task: Task, current: User) -> bool:
    """Правило карточки: своя задача и та, где ты исполнитель."""
    return (
        has_permission(current, "workspace.manage")
        or task.created_by_id == current.id
        or any(link.user_id == current.id for link in task.assignees)
    )


def _can_edit_task(task: Task, current: User, rights: dict[uuid.UUID, dict]) -> bool:
    """Правило карточки плюс право раздела.

    Право «правка чужих» в разделе правило карточки расширяет — так тимлид
    отдела ведёт доску отдела, не получая власти над всем воркспейсом. Удаление
    из него не следует: чужую карточку чаще нужно поправить, чем стереть.
    """
    return _owns_task(task, current) or rights.get(task.section_id, DEFAULT_ACCESS)["can_edit"]


def _can_delete_task(task: Task, current: User, rights: dict[uuid.UUID, dict]) -> bool:
    return (
        _owns_task(task, current)
        or rights.get(task.section_id, DEFAULT_ACCESS)["can_delete"]
    )


def _assert_task_editable(
    task: Task, current: User, rights: dict[uuid.UUID, dict], *, mode: str = "edit"
) -> None:
    allowed = (
        _can_delete_task(task, current, rights)
        if mode == "delete"
        else _can_edit_task(task, current, rights)
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Недостаточно прав для изменения задачи")


async def _lock_statuses(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    status_ids: set[uuid.UUID],
) -> None:
    if not status_ids:
        return
    # Stable ordering avoids two cross-column moves taking the same locks in
    # opposite order.  The query result is intentionally consumed so the lock
    # is acquired before positions are read or rewritten.
    await db.execute(
        select(TaskStatus.id)
        .where(
            TaskStatus.workspace_id == workspace_id,
            TaskStatus.id.in_(status_ids),
        )
        .order_by(TaskStatus.id)
        .with_for_update()
    )


async def _template(db: AsyncSession, current: User, template_id: uuid.UUID) -> TaskTemplate:
    template = await db.get(TaskTemplate, template_id)
    if not template or template.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    return template


async def _next_position(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    section_id: uuid.UUID,
    status_id: uuid.UUID,
) -> int:
    await _lock_statuses(db, workspace_id, {status_id})
    last = await db.scalar(
        select(func.max(Task.position)).where(
            Task.workspace_id == workspace_id,
            Task.section_id == section_id,
            Task.status_id == status_id,
        )
    )
    return (last or 0) + POSITION_STEP


async def _valid_status_id(
    db: AsyncSession,
    current: User,
    status_id: uuid.UUID | None,
) -> uuid.UUID | None:
    if status_id is None:
        return None
    status = await db.get(TaskStatus, status_id)
    if not status or status.workspace_id != current.workspace_id:
        # A foreign tenant's UUID must never be persisted in a local template.
        raise HTTPException(status_code=422, detail="Такой колонки на доске нет")
    return status.id


async def _valid_assignees(
    db: AsyncSession, current: User, ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    found = set(
        (
            await db.execute(
                select(User.id).where(
                    User.id.in_(wanted), User.workspace_id == current.workspace_id
                )
            )
        ).scalars()
    )
    missing = [value for value in wanted if value not in found]
    if missing:
        raise HTTPException(status_code=422, detail="Такого исполнителя нет в команде")
    return wanted


def _check_task_dates(start: date | None, due: date | None) -> None:
    """Начать позже срока нельзя — это не задача, а опечатка."""
    if start and due and start > due:
        raise HTTPException(
            status_code=422,
            detail="Дата начала не может быть позже срока выполнения",
        )


def _required_text(value: str | None, label: str) -> str:
    clean = (value or "").strip()
    if not clean:
        raise HTTPException(status_code=422, detail=f"{label} не может быть пустым")
    return clean


async def _user_names(db: AsyncSession, workspace_id: uuid.UUID) -> dict[uuid.UUID, str]:
    rows = await db.execute(
        select(User.id, User.name).where(User.workspace_id == workspace_id)
    )
    return {row.id: row.name for row in rows}


def _apply_template(template: TaskTemplate, values: dict, provided: set[str]) -> dict:
    """Шаблон подставляет только значения своих полей.

    Стандартные поля карточки — название, приоритет, колонка, срок, описание,
    исполнители — шаблон не трогает: их всё равно заполняют под конкретную
    задачу, а подставленное «Крео для » приходилось стирать перед каждым вводом.
    """
    result = dict(values)
    if "custom_values" not in provided and template.custom_values:
        result["custom_values"] = dict(template.custom_values)
    return result


def _valid_field_ids(
    field_ids: list[uuid.UUID] | list[str], fields: list[TaskField]
) -> list[str]:
    """Только существующие поля, без повторов, в порядке, заданном шаблоном."""
    known = {str(field.id) for field in fields}
    result: list[str] = []
    for raw in field_ids or []:
        key = str(raw)
        if key not in known:
            raise HTTPException(
                status_code=422, detail="В шаблоне указано несуществующее поле"
            )
        if key not in result:
            result.append(key)
    return result


def _pick_status(statuses: list[TaskStatus], status_id: uuid.UUID | None) -> TaskStatus:
    if status_id:
        for status in statuses:
            if status.id == status_id:
                return status
        raise HTTPException(status_code=422, detail="Такой колонки на доске нет")
    return statuses[0]


def _visible_fields(
    fields: list[TaskField],
    template: TaskTemplate | None,
    values: dict | None = None,
) -> list[TaskField]:
    """Какие поля показывает эта карточка и в каком порядке.

    Порядок задаёт шаблон: бриф читают сверху вниз, и «ТЗ» под «Референсом» —
    это не то же самое, что наоборот. Дальше идут общие поля доски.

    Поле с уже заполненным значением остаётся в карточке, даже если его убрали
    из шаблона: иначе значение продолжало бы лежать в базе, но исчезло бы с
    экрана, и никто не смог бы его ни увидеть, ни стереть.
    """
    by_id = {str(field.id): field for field in fields}
    ordered: list[TaskField] = []
    seen: set[str] = set()
    for raw in (template.field_ids if template else []) or []:
        field = by_id.get(str(raw))
        if field is not None and str(field.id) not in seen:
            seen.add(str(field.id))
            ordered.append(field)
    for field in fields:
        key = str(field.id)
        if key in seen:
            continue
        if field.show_always or key in (values or {}):
            seen.add(key)
            ordered.append(field)
    return ordered


def _clean_custom_values(
    values: dict,
    fields: list[TaskField],
    *,
    allow_files: bool = True,
    require: bool = True,
) -> dict:
    """Оставить только значения показанных полей, приведённые к типу поля.

    `fields` — уже отфильтрованный набор для этой карточки: проверять
    обязательность поля, которого в карточке нет, нельзя, иначе задача из
    одного шаблона перестала бы сохраняться из-за поля другого.

    `require=False` для шаблонов: шаблон хранит значения по умолчанию, и
    требовать в нём заполнения обязательного поля — значит запретить шаблон,
    который специально оставляет это поле исполнителю.
    """
    known = {str(field.id): field for field in fields}
    result: dict = {}
    for key, value in (values or {}).items():
        field = known.get(str(key))
        if field is None or value is None or value == "":
            continue
        if field.kind == "number":
            try:
                result[str(field.id)] = float(value)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=422, detail=f"Поле «{field.name}» ждёт число"
                ) from None
        elif field.kind == "money":
            # Строкой, а не float: 0.1 + 0.2 в двоичной дроби не даёт 0.3, а это
            # деньги — расхождение всплывёт в отчёте, а не в тесте.
            try:
                amount = Decimal(str(value).replace(",", ".").replace(" ", ""))
            except InvalidOperation:
                raise HTTPException(
                    status_code=422, detail=f"Поле «{field.name}» ждёт сумму"
                ) from None
            result[str(field.id)] = str(amount.quantize(Decimal("0.01")))
        elif field.kind == "checkbox":
            result[str(field.id)] = bool(value)
        elif field.kind == "select":
            if str(value) not in (field.options or []):
                raise HTTPException(
                    status_code=422,
                    detail=f"«{value}» нет в списке значений поля «{field.name}»",
                )
            result[str(field.id)] = str(value)
        elif field.kind == "labels":
            if not isinstance(value, list):
                value = [value]
            picked: list[str] = []
            for item in value[:MAX_LABELS]:
                text = str(item)
                if text not in (field.options or []):
                    raise HTTPException(
                        status_code=422,
                        detail=f"«{text}» нет в списке значений поля «{field.name}»",
                    )
                if text not in picked:
                    picked.append(text)
            if not picked:
                continue
            result[str(field.id)] = picked
        elif field.kind == "date":
            try:
                result[str(field.id)] = date.fromisoformat(str(value)[:10]).isoformat()
            except ValueError:
                raise HTTPException(
                    status_code=422, detail=f"Поле «{field.name}» ждёт дату"
                ) from None
        elif field.kind == "user":
            try:
                result[str(field.id)] = str(uuid.UUID(str(value)))
            except ValueError:
                raise HTTPException(
                    status_code=422, detail=f"Поле «{field.name}» ждёт пользователя"
                ) from None
        elif field.kind == "url":
            text = str(value).strip()[:2000]
            if not text.lower().startswith(("http://", "https://")):
                raise HTTPException(
                    status_code=422,
                    detail=f"Поле «{field.name}» ждёт ссылку, начинающуюся с http:// или https://",
                )
            result[str(field.id)] = text
        elif field.kind == "file":
            # У шаблона файлового значения по умолчанию быть не может: файл
            # принадлежит одной задаче, и общий на всех он бы либо дублировался,
            # либо исчез при первой же правке любой из карточек.
            if not allow_files:
                continue
            if not isinstance(value, list):
                value = [value]
            limit = int((field.config or {}).get("max_files", 10))
            picked_files: list[str] = []
            for item in value[:limit]:
                try:
                    attachment_id = str(uuid.UUID(str(item)))
                except ValueError:
                    raise HTTPException(
                        status_code=422, detail=f"Поле «{field.name}» ждёт файлы"
                    ) from None
                if attachment_id not in picked_files:
                    picked_files.append(attachment_id)
            if not picked_files:
                continue
            result[str(field.id)] = picked_files
        else:
            result[str(field.id)] = str(value)[:2000]

    if require:
        for field in fields:
            if field.is_required and str(field.id) not in result:
                raise HTTPException(
                    status_code=422, detail=f"Заполните поле «{field.name}»"
                )
    return result




def _status_code(name: str, taken: set[str]) -> str:
    base = "".join(
        char if char.isalnum() else "_" for char in name.strip().lower()
    ).strip("_")[:30] or "status"
    code = base
    suffix = 2
    while code in taken:
        code = f"{base}_{suffix}"
        suffix += 1
    return code


def _task_row(
    task: Task,
    names: dict[uuid.UUID, str],
    current: User,
    fields: list[TaskField] | None = None,
    templates: dict[uuid.UUID, TaskTemplate] | None = None,
    rights: dict[uuid.UUID, dict] | None = None,
) -> dict:
    rights = rights or {}
    can_edit = _can_edit_task(task, current, rights)
    template = (templates or {}).get(task.template_id) if task.template_id else None
    visible = _visible_fields(fields or [], template, task.custom_values or {})
    return {
        "id": str(task.id),
        "title": task.title,
        "description": task.description,
        "section_id": str(task.section_id),
        "template_id": str(task.template_id) if task.template_id else None,
        # Порядок полей именно этой карточки — считает сервер, чтобы бриф
        # выглядел одинаково в интерфейсе и в любом другом клиенте API.
        "field_ids": [str(field.id) for field in visible],
        "status_id": str(task.status_id),
        "priority": task.priority.value,
        "start_date": task.start_date.isoformat() if task.start_date else None,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "position": task.position,
        "custom_values": task.custom_values or {},
        "assignee_ids": [str(link.user_id) for link in task.assignees],
        "assignees": [
            {"id": str(link.user_id), "name": names.get(link.user_id, "—")}
            for link in task.assignees
        ],
        "is_done": task.completed_at is not None,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "can_edit": can_edit,
        "can_delete": _can_delete_task(task, current, rights),
    }


def _field_row(field: TaskField) -> dict:
    return {
        "id": str(field.id),
        "name": field.name,
        "kind": field.kind,
        "config": field.config or {},
        "show_always": field.show_always,
        "options": field.options or [],
        "is_required": field.is_required,
        "position": field.position,
    }


def _template_row(template: TaskTemplate) -> dict:
    return {
        "id": str(template.id),
        "name": template.name,
        "custom_values": template.custom_values or {},
        "field_ids": [str(value) for value in (template.field_ids or [])],
        "is_default": template.is_default,
    }


def _sort_rows(rows: list[dict], sort: str) -> None:
    """Сортировка карточек внутри колонки.

    По сроку — просроченные и ближайшие сверху, задачи без срока в конце: иначе
    пустой срок оказывается «самым ранним» и заслоняет то, что горит.
    """
    if sort == "due_date":
        rows.sort(key=lambda row: (row["due_date"] is None, row["due_date"] or ""))
    elif sort == "priority":
        rows.sort(key=lambda row: PRIORITY_ORDER[TaskPriority(row["priority"])])
    # Позиция во втором ключе — не украшение: две задачи, заведённые в одну
    # секунду, иначе встают в произвольном порядке, и он меняется от запроса к
    # запросу.
    elif sort == "created":
        rows.sort(key=lambda row: (row["created_at"], row["position"]), reverse=True)
    elif sort == "created_asc":
        rows.sort(key=lambda row: (row["created_at"], row["position"]))
    elif sort == "updated":
        rows.sort(key=lambda row: (row["updated_at"], row["position"]), reverse=True)
    elif sort == "updated_asc":
        rows.sort(key=lambda row: (row["updated_at"], row["position"]))
    else:
        rows.sort(key=lambda row: row["position"])
