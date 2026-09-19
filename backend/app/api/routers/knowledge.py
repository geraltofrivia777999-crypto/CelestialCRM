"""База знаний — ТЗ 8.2.

Права здесь двухслойные: `knowledge.view` открывает сам раздел CRM, а дальше
каждый раздел базы может ограничивать роли по просмотру, созданию, правке,
удалению и управлению доступом. Правило наследуется вниз по дереву, раздел без
правил открыт всем — иначе первый созданный раздел был бы невидим даже автору.

Черновики видит только тот, кто может править раздел: смысл черновика в том, что
его ещё не показывают команде.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import has_full_access, has_permission, require_permission
from app.models import (
    ArticleStatus,
    KnowledgeAccess,
    KnowledgeArticle,
    KnowledgeAttachment,
    KnowledgeSection,
    Role,
    User,
)
from app.schemas import (
    KnowledgeAccessUpdate,
    KnowledgeArticleCreate,
    KnowledgeArticleUpdate,
    KnowledgeSectionCreate,
    KnowledgeSectionUpdate,
    Page,
)
from app.services import attachments, storage
from app.services.audit import audit
from app.services.knowledge import (
    DEFAULT_ACCESS,
    FULL_ACCESS,
    blocks_to_text,
    normalize_blocks,
    section_rights,
    used_attachment_ids,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

MAX_DEPTH = 5


@router.get("/tree")
async def tree(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.view")),
) -> dict:
    """Дерево разделов со статьями, обрезанное по правам роли."""
    admin = await _is_admin(db, current)
    rights = await section_rights(db, current, is_admin=admin)
    sections = list(
        (
            await db.execute(
                select(KnowledgeSection)
                .where(KnowledgeSection.workspace_id == current.workspace_id)
                .order_by(KnowledgeSection.position, KnowledgeSection.title)
            )
        ).scalars()
    )
    visible = {
        section.id
        for section in sections
        if rights.get(section.id, DEFAULT_ACCESS)["can_view"]
    }
    articles = list(
        (
            await db.execute(
                select(KnowledgeArticle)
                .where(KnowledgeArticle.workspace_id == current.workspace_id)
                .order_by(KnowledgeArticle.position, KnowledgeArticle.title)
            )
        ).scalars()
    )

    by_section: dict[uuid.UUID | None, list[dict]] = {}
    for article in articles:
        if article.section_id is not None and article.section_id not in visible:
            continue
        if not _can_see_article(article, rights, current, admin):
            continue
        by_section.setdefault(article.section_id, []).append(_article_stub(article))

    nodes = {
        section.id: {
            "id": str(section.id),
            "parent_id": str(section.parent_id) if section.parent_id else None,
            "title": section.title,
            "icon": section.icon,
            "position": section.position,
            "rights": rights.get(section.id, DEFAULT_ACCESS),
            "articles": by_section.get(section.id, []),
            "children": [],
        }
        for section in sections
        if section.id in visible
    }
    roots = []
    for section in sections:
        node = nodes.get(section.id)
        if not node:
            continue
        parent = nodes.get(section.parent_id) if section.parent_id else None
        (parent["children"] if parent else roots).append(node)

    return {
        "sections": roots,
        "loose_articles": by_section.get(None, []),
        "can_manage": admin or has_permission(current, "knowledge.manage"),
    }


@router.post("/sections", status_code=201)
async def create_section(
    payload: KnowledgeSectionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.manage")),
) -> dict:
    if payload.parent_id:
        parent = await _section(db, current, payload.parent_id)
        if await _depth(db, parent) >= MAX_DEPTH:
            raise HTTPException(
                status_code=422,
                detail=f"Глубже {MAX_DEPTH} уровней дерево становится нечитаемым",
            )
    position = await db.scalar(
        select(func.count())
        .select_from(KnowledgeSection)
        .where(
            KnowledgeSection.workspace_id == current.workspace_id,
            KnowledgeSection.parent_id == payload.parent_id,
        )
    )
    section = KnowledgeSection(
        workspace_id=current.workspace_id,
        parent_id=payload.parent_id,
        title=_required_title(payload.title, "раздела"),
        icon=payload.icon,
        position=position or 0,
        created_by_id=current.id,
    )
    db.add(section)
    await audit(
        db, current, "knowledge.section_created", f"Создан раздел базы знаний «{section.title}»",
        request=request,
    )
    await db.commit()
    await db.refresh(section)
    return _section_row(section, dict(FULL_ACCESS))


@router.patch("/sections/{section_id}")
async def update_section(
    section_id: uuid.UUID,
    payload: KnowledgeSectionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.manage")),
) -> dict:
    section = await _section(db, current, section_id)
    changes = payload.model_dump(exclude_unset=True)
    if "parent_id" in changes:
        parent_id = changes.pop("parent_id")
        if parent_id == section.id:
            raise HTTPException(status_code=422, detail="Раздел не может лежать сам в себе")
        if parent_id:
            parent = await _section(db, current, parent_id)
            if await _is_descendant(db, section.id, parent_id):
                raise HTTPException(
                    status_code=422,
                    detail="Нельзя перенести раздел внутрь его собственной ветки",
                )
            if await _depth(db, parent) + await _subtree_height(db, section) > MAX_DEPTH:
                raise HTTPException(
                    status_code=422,
                    detail=f"Глубже {MAX_DEPTH} уровней дерево становится нечитаемым",
                )
        section.parent_id = parent_id
    if "title" in changes:
        title = changes.pop("title")
        if title is not None:
            section.title = _required_title(title, "раздела")
    for field, value in changes.items():
        if value is not None:
            setattr(section, field, value)
    await audit(
        db, current, "knowledge.section_updated", f"Изменён раздел «{section.title}»",
        request=request, entity_id=str(section.id),
    )
    await db.commit()
    return _section_row(section, dict(FULL_ACCESS))


@router.delete("/sections/{section_id}")
async def delete_section(
    section_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.manage")),
) -> dict:
    section = await _section(db, current, section_id)
    articles = await db.scalar(
        select(func.count())
        .select_from(KnowledgeArticle)
        .where(KnowledgeArticle.section_id == section.id)
    )
    children = await db.scalar(
        select(func.count())
        .select_from(KnowledgeSection)
        .where(KnowledgeSection.parent_id == section.id)
    )
    if articles or children:
        raise HTTPException(
            status_code=422,
            detail=(
                f"В разделе {articles} статей и {children} вложенных разделов. "
                "Перенесите или удалите их — молча стирать документацию нельзя."
            ),
        )
    title = section.title
    await db.delete(section)
    await audit(
        db, current, "knowledge.section_deleted", f"Удалён раздел «{title}»",
        request=request, entity_id=str(section_id),
    )
    await db.commit()
    return {"deleted": str(section_id)}


@router.get("/sections/{section_id}/access")
async def section_access(
    section_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.manage")),
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
                select(KnowledgeAccess).where(KnowledgeAccess.section_id == section.id)
            )
        ).scalars()
    )
    by_role = {row.role_id: row for row in existing if row.role_id is not None}
    by_user = {row.user_id: row for row in existing if row.user_id is not None}

    def entry(rule: KnowledgeAccess | None) -> dict:
        return {
            "configured": rule is not None,
            "can_view": rule.can_view if rule else True,
            "can_create": rule.can_create if rule else False,
            "can_edit": rule.can_edit if rule else False,
            "can_delete": rule.can_delete if rule else False,
            "can_manage": rule.can_manage if rule else False,
        }

    return {
        "section_id": str(section.id),
        "title": section.title,
        "inherited": not existing,
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
    payload: KnowledgeAccessUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.manage")),
) -> dict:
    """Заменить правила доступа к разделу.

    Пустой список означает «вернуть наследование от родителя», а не «закрыть
    всем»: закрытие раздела для всех ролей должно быть явным действием.
    """
    section = await _section(db, current, section_id)
    role_ids = set(
        (
            await db.execute(
                select(Role.id).where(Role.workspace_id == current.workspace_id)
            )
        ).scalars()
    )
    user_ids = set(
        (
            await db.execute(
                select(User.id).where(User.workspace_id == current.workspace_id)
            )
        ).scalars()
    )
    seen: set[tuple[str, uuid.UUID]] = set()
    for rule in payload.rules:
        if rule.role_id is not None and rule.role_id not in role_ids:
            raise HTTPException(status_code=422, detail="Такой роли в воркспейсе нет")
        if rule.user_id is not None and rule.user_id not in user_ids:
            raise HTTPException(
                status_code=422, detail="Такого пользователя в воркспейсе нет"
            )
        key = ("role", rule.role_id) if rule.role_id else ("user", rule.user_id)
        if key in seen:
            raise HTTPException(
                status_code=422, detail="Для одного адресата задано два правила"
            )
        seen.add(key)

    existing = list(
        (
            await db.execute(
                select(KnowledgeAccess).where(KnowledgeAccess.section_id == section.id)
            )
        ).scalars()
    )
    for row in existing:
        await db.delete(row)
    await db.flush()
    for rule in payload.rules:
        db.add(
            KnowledgeAccess(
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
        db, current, "knowledge.access_updated",
        f"Изменён доступ к разделу «{section.title}»: правил {len(payload.rules)}",
        request=request, entity_id=str(section.id),
    )
    await db.commit()
    return {"section_id": str(section.id), "rules": len(payload.rules)}


@router.get("/articles/search", response_model=Page)
async def search_articles(
    q: str = "",
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.view")),
) -> Page:
    """Поиск по названию и содержимому — ТЗ 8.2."""
    query = q.strip()
    if len(query) < 2:
        return Page(items=[], total=0, limit=0, offset=0)
    result_limit = max(0, min(limit, 100))
    if result_limit == 0:
        return Page(items=[], total=0, limit=0, offset=0)
    admin = await _is_admin(db, current)
    rights = await section_rights(db, current, is_admin=admin)
    # SQLite's LIKE/LOWER only case-fold ASCII and PostgreSQL behaviour depends
    # on database collation.  Apply Unicode casefold after the workspace/status
    # filters, then stop at the same API limit.  This keeps Cyrillic search
    # deterministic without letting inaccessible articles influence the limit.
    needle = query.casefold()
    rows = list(
        (
            await db.execute(
                select(KnowledgeArticle)
                .where(
                    KnowledgeArticle.workspace_id == current.workspace_id,
                    KnowledgeArticle.status != ArticleStatus.archived,
                )
                .order_by(KnowledgeArticle.updated_at.desc())
            )
        ).scalars()
    )
    items = []
    for article in rows:
        if (
            needle not in article.title.casefold()
            and needle not in (article.search_text or "").casefold()
        ):
            continue
        if not _can_see_article(article, rights, current, admin):
            continue
        items.append({**_article_stub(article), "excerpt": _excerpt(article, query)})
        if len(items) >= result_limit:
            break
    return Page(items=items, total=len(items), limit=result_limit, offset=0)


@router.get("/articles/{article_id}")
async def read_article(
    article_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.view")),
) -> dict:
    admin = await _is_admin(db, current)
    rights = await section_rights(db, current, is_admin=admin)
    article = await _article(db, current, article_id)
    access = _article_rights(article, rights, admin)
    if not access["can_view"] or not _can_see_article(article, rights, current, admin):
        raise HTTPException(status_code=404, detail="Статья не найдена")
    names = await _user_names(db, current.workspace_id)
    children = list(
        (
            await db.execute(
                select(KnowledgeArticle)
                .where(KnowledgeArticle.parent_id == article.id)
                .order_by(KnowledgeArticle.position)
            )
        ).scalars()
    )
    return {
        **_article_stub(article),
        # Повторная нормализация очищает и старые статьи, сохранённые до того,
        # как служебные div/p редактора стали преобразовываться в переносы.
        "blocks": normalize_blocks(article.blocks or []),
        "created_by": names.get(article.created_by_id),
        "updated_by": names.get(article.updated_by_id),
        "children": [_article_stub(child) for child in children],
        "rights": access,
    }


@router.post("/articles", status_code=201)
async def create_article(
    payload: KnowledgeArticleCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.view")),
) -> dict:
    admin = await _is_admin(db, current)
    rights = await section_rights(db, current, is_admin=admin)
    if payload.section_id:
        await _section(db, current, payload.section_id)
        if not rights.get(payload.section_id, DEFAULT_ACCESS)["can_create"]:
            raise HTTPException(
                status_code=403, detail="Нет права создавать статьи в этом разделе"
            )
    elif not admin and not has_permission(current, "knowledge.manage"):
        raise HTTPException(status_code=403, detail="Выберите раздел для статьи")
    if payload.parent_id:
        await _validate_article_parent(
            db,
            current,
            payload.parent_id,
            section_id=payload.section_id,
        )

    blocks = normalize_blocks(payload.blocks)
    position = await db.scalar(
        select(func.count())
        .select_from(KnowledgeArticle)
        .where(
            KnowledgeArticle.workspace_id == current.workspace_id,
            KnowledgeArticle.section_id == payload.section_id,
        )
    )
    article = KnowledgeArticle(
        workspace_id=current.workspace_id,
        section_id=payload.section_id,
        parent_id=payload.parent_id,
        title=_required_title(payload.title, "статьи"),
        blocks=blocks,
        search_text=blocks_to_text(blocks),
        status=payload.status,
        position=position or 0,
        created_by_id=current.id,
        updated_by_id=current.id,
        published_at=(
            datetime.now(UTC) if payload.status == ArticleStatus.published else None
        ),
    )
    db.add(article)
    await db.flush()
    await _bind_attachments(db, current, article, blocks)
    await audit(
        db, current, "knowledge.article_created", f"Создана статья «{article.title}»",
        request=request, entity_id=str(article.id),
    )
    await db.commit()
    await db.refresh(article)
    return {**_article_stub(article), "blocks": article.blocks or []}


@router.patch("/articles/{article_id}")
async def update_article(
    article_id: uuid.UUID,
    payload: KnowledgeArticleUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.view")),
) -> dict:
    admin = await _is_admin(db, current)
    rights = await section_rights(db, current, is_admin=admin)
    article = await _article(db, current, article_id)
    if not _article_rights(article, rights, admin)["can_edit"]:
        raise HTTPException(status_code=403, detail="Нет права редактировать эту статью")

    changes = payload.model_dump(exclude_unset=True)
    target_section_id = changes.get("section_id", article.section_id)
    target_parent_id = changes.get("parent_id", article.parent_id)
    if "section_id" in changes:
        if target_section_id:
            await _section(db, current, target_section_id)
            if not rights.get(target_section_id, DEFAULT_ACCESS)["can_create"]:
                raise HTTPException(
                    status_code=403, detail="Нет права переносить статьи в этот раздел"
                )
        elif not admin and not has_permission(current, "knowledge.manage"):
            raise HTTPException(
                status_code=403,
                detail="Нет права переносить статью за пределы раздела",
            )
        if target_section_id != article.section_id:
            children = await db.scalar(
                select(func.count())
                .select_from(KnowledgeArticle)
                .where(KnowledgeArticle.parent_id == article.id)
            )
            if children:
                raise HTTPException(
                    status_code=422,
                    detail="Сначала перенесите вложенные страницы статьи",
                )
    if target_parent_id:
        await _validate_article_parent(
            db,
            current,
            target_parent_id,
            section_id=target_section_id,
            article_id=article.id,
        )

    if "section_id" in changes:
        article.section_id = changes.pop("section_id")
    if "parent_id" in changes:
        article.parent_id = changes.pop("parent_id")
    removed_storage_paths: list[str] = []
    if "blocks" in changes:
        blocks = normalize_blocks(changes.pop("blocks"))
        article.blocks = blocks
        article.search_text = blocks_to_text(blocks)
        removed_storage_paths = await _bind_attachments(db, current, article, blocks)
    if "status" in changes and changes["status"]:
        status = changes.pop("status")
        if status == ArticleStatus.published and article.status != ArticleStatus.published:
            article.published_at = datetime.now(UTC)
        article.status = status
    if "title" in changes:
        title = changes.pop("title")
        if title is not None:
            article.title = _required_title(title, "статьи")
    for field, value in changes.items():
        if value is not None:
            setattr(article, field, value)
    article.updated_by_id = current.id

    await audit(
        db, current, "knowledge.article_updated", f"Изменена статья «{article.title}»",
        request=request, entity_id=str(article.id),
    )
    await db.commit()
    await db.refresh(article)
    # Removing a block is an explicit deletion of its private attachment.  The
    # bytes are removed only after the database commit, so a failed edit cannot
    # leave the article pointing at a missing file.
    for storage_path in removed_storage_paths:
        storage.remove(storage_path)
    return {**_article_stub(article), "blocks": article.blocks or []}


@router.delete("/articles/{article_id}")
async def delete_article(
    article_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.view")),
) -> dict:
    admin = await _is_admin(db, current)
    rights = await section_rights(db, current, is_admin=admin)
    article = await _article(db, current, article_id)
    if not _article_rights(article, rights, admin)["can_delete"]:
        raise HTTPException(status_code=403, detail="Нет права удалять эту статью")
    children = await db.scalar(
        select(func.count())
        .select_from(KnowledgeArticle)
        .where(KnowledgeArticle.parent_id == article.id)
    )
    if children:
        raise HTTPException(
            status_code=422,
            detail=f"У статьи {children} вложенных страниц — сначала уберите их",
        )
    attachments = list(
        (
            await db.execute(
                select(KnowledgeAttachment).where(
                    KnowledgeAttachment.article_id == article.id
                )
            )
        ).scalars()
    )
    title = article.title
    # Do not rely only on database ON DELETE CASCADE: production PostgreSQL
    # enforces it, while development SQLite may not.  Explicit deletion keeps
    # metadata and filesystem cleanup identical in both environments.
    for attachment in attachments:
        await db.delete(attachment)
    await db.delete(article)
    await audit(
        db, current, "knowledge.article_deleted", f"Удалена статья «{title}»",
        request=request, entity_id=str(article_id),
    )
    await db.commit()
    # Файлы удаляем после коммита: если транзакция откатится, статья останется,
    # а вложения уже исчезли бы с диска.
    for attachment in attachments:
        storage.remove(attachment.storage_path)
    return {"deleted": str(article_id)}


@router.post("/attachments", status_code=201)
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
    section_id: uuid.UUID | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.view")),
) -> dict:
    """Загрузить файл для статьи в разделе, где пользователь может её создать.

    ``section_id`` optional for backward compatibility and for loose articles,
    but only an administrator or ``knowledge.manage`` holder can omit it.
    """
    admin = await _is_admin(db, current)
    if section_id:
        await _section(db, current, section_id)
        rights = await section_rights(db, current, is_admin=admin)
        if not rights.get(section_id, DEFAULT_ACCESS)["can_create"]:
            raise HTTPException(
                status_code=403, detail="Нет права загружать файлы в этот раздел"
            )
    elif not admin and not has_permission(current, "knowledge.manage"):
        raise HTTPException(
            status_code=403,
            detail="Выберите раздел, в котором разрешено создавать статьи",
        )

    attachment, stale_attachments = await attachments.receive_upload(db, current, file)
    path = attachment.storage_path
    try:
        await audit(
            db, current, "knowledge.attachment_uploaded",
            f"Загружено вложение «{attachment.file_name}»", request=request,
        )
        await db.commit()
        await db.refresh(attachment)
    except Exception:
        await db.rollback()
        storage.remove(path)
        raise
    # Stale records and the new upload were committed atomically; only now is it
    # safe to remove the old bytes from disk.
    for stale in stale_attachments:
        storage.remove(stale.storage_path)
    return attachments.row(attachment, "/api/v1/knowledge/attachments")


@router.delete("/attachments/{attachment_id}")
async def delete_unbound_attachment(
    attachment_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.view")),
) -> dict:
    """Discard an upload when the editor is closed before the article is saved."""
    attachment = await db.get(KnowledgeAttachment, attachment_id)
    if not attachment or attachment.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    if attachment.article_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Вложение используется в статье — удалите из неё блок с файлом",
        )
    if attachment.task_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Вложение прикреплено к задаче — удалите его из карточки",
        )
    admin = await _is_admin(db, current)
    if (
        attachment.uploaded_by_id != current.id
        and not admin
        and not has_permission(current, "knowledge.manage")
    ):
        raise HTTPException(status_code=404, detail="Вложение не найдено")

    storage_path = attachment.storage_path
    file_name = attachment.file_name
    await db.delete(attachment)
    await audit(
        db,
        current,
        "knowledge.attachment_deleted",
        f"Удалено несвязанное вложение «{file_name}»",
        request=request,
        entity_id=str(attachment_id),
    )
    await db.commit()
    storage.remove(storage_path)
    return {"deleted": str(attachment_id)}


@router.get("/attachments/{attachment_id}")
async def read_attachment(
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("knowledge.view")),
) -> Response:
    """Отдать файл вложения.

    Файл проходит через проверку прав на раздел статьи: раздача напрямую с диска
    позволила бы прочитать закрытый раздел по ссылке из чужой статьи.
    """
    attachment = await db.get(KnowledgeAttachment, attachment_id)
    if not attachment or attachment.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    if attachment.article_id:
        admin = await _is_admin(db, current)
        rights = await section_rights(db, current, is_admin=admin)
        article = await db.get(KnowledgeArticle, attachment.article_id)
        if not article or not _can_see_article(article, rights, current, admin):
            raise HTTPException(status_code=404, detail="Вложение не найдено")
    elif attachment.uploaded_by_id != current.id:
        admin = await _is_admin(db, current)
        if not admin and not has_permission(current, "knowledge.manage"):
            raise HTTPException(status_code=404, detail="Вложение не найдено")
    try:
        path = storage.resolve(attachment.storage_path)
    except storage.StorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=path.read_bytes(),
        media_type=attachment.mime_type,
        headers={
            # inline для картинок и видео, но с запретом угадывания типа: файл,
            # который браузер решит считать HTML, выполнит скрипты в нашем домене.
            "Content-Disposition": storage.disposition(
                attachment.file_name,
                inline=storage.kind_for(attachment.mime_type) != "file",
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )


async def _section(
    db: AsyncSession, current: User, section_id: uuid.UUID
) -> KnowledgeSection:
    section = await db.get(KnowledgeSection, section_id)
    if not section or section.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Раздел не найден")
    return section


async def _article(
    db: AsyncSession, current: User, article_id: uuid.UUID
) -> KnowledgeArticle:
    article = await db.get(KnowledgeArticle, article_id)
    if not article or article.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Статья не найдена")
    return article


async def _is_admin(db: AsyncSession, current: User) -> bool:
    """Knowledge administrators bypass per-section ACLs.

    ``knowledge.manage`` is the module-wide management permission, not merely
    permission to open the ACL dialog.  Treating only the global ``*`` role as
    privileged made Team Lead unable to author content in newly created
    sections and unable to edit its own loose articles.
    """
    return has_permission(current, "knowledge.manage") or await has_full_access(
        db, current
    )


async def _depth(db: AsyncSession, section: KnowledgeSection) -> int:
    depth = 1
    current = section
    while current.parent_id and depth < MAX_DEPTH + 2:
        current = await db.get(KnowledgeSection, current.parent_id)
        if not current:
            break
        depth += 1
    return depth


async def _subtree_height(db: AsyncSession, section: KnowledgeSection) -> int:
    """Height of a section branch, used to enforce MAX_DEPTH after a move."""
    rows = await db.execute(
        select(KnowledgeSection.id, KnowledgeSection.parent_id).where(
            KnowledgeSection.workspace_id == section.workspace_id
        )
    )
    children: dict[uuid.UUID, list[uuid.UUID]] = {}
    for row in rows:
        if row.parent_id is not None:
            children.setdefault(row.parent_id, []).append(row.id)

    height = 0
    frontier = [section.id]
    seen: set[uuid.UUID] = set()
    while frontier and height <= MAX_DEPTH + 1:
        height += 1
        next_frontier = []
        for section_id in frontier:
            if section_id in seen:
                continue
            seen.add(section_id)
            next_frontier.extend(children.get(section_id, []))
        frontier = next_frontier
    return height


async def _is_descendant(
    db: AsyncSession, section_id: uuid.UUID, candidate_id: uuid.UUID
) -> bool:
    current = await db.get(KnowledgeSection, candidate_id)
    hops = 0
    while current and hops < 50:
        if current.id == section_id:
            return True
        if not current.parent_id:
            return False
        current = await db.get(KnowledgeSection, current.parent_id)
        hops += 1
    return False


async def _validate_article_parent(
    db: AsyncSession,
    current: User,
    parent_id: uuid.UUID,
    *,
    section_id: uuid.UUID | None,
    article_id: uuid.UUID | None = None,
) -> KnowledgeArticle:
    """Validate tenant, cycle and section invariants for an article parent."""
    parent = await _article(db, current, parent_id)
    if article_id and (
        parent.id == article_id
        or await _is_article_descendant(db, article_id, parent.id)
    ):
        raise HTTPException(
            status_code=422,
            detail="Статья не может быть вложена в саму себя или свою дочернюю страницу",
        )
    if parent.section_id != section_id:
        raise HTTPException(
            status_code=422,
            detail="Родительская и вложенная статьи должны находиться в одном разделе",
        )
    return parent


async def _is_article_descendant(
    db: AsyncSession, article_id: uuid.UUID, candidate_id: uuid.UUID
) -> bool:
    current = await db.get(KnowledgeArticle, candidate_id)
    seen: set[uuid.UUID] = set()
    while current and current.id not in seen:
        if current.id == article_id:
            return True
        seen.add(current.id)
        if not current.parent_id:
            return False
        current = await db.get(KnowledgeArticle, current.parent_id)
    return False


async def _bind_attachments(
    db: AsyncSession, current: User, article: KnowledgeArticle, blocks: list[dict]
) -> list[str]:
    """Bind valid uploads and return paths explicitly removed from the article."""
    wanted = used_attachment_ids(blocks)
    conditions = [KnowledgeAttachment.article_id == article.id]
    if wanted:
        conditions.append(KnowledgeAttachment.id.in_(wanted))
    rows = list(
        (
            await db.execute(
                select(KnowledgeAttachment).where(
                    KnowledgeAttachment.workspace_id == current.workspace_id,
                    or_(*conditions),
                )
            )
        ).scalars()
    )
    by_id = {attachment.id: attachment for attachment in rows}
    privileged = await _is_admin(db, current) or has_permission(
        current, "knowledge.manage"
    )
    for attachment_id in wanted:
        attachment = by_id.get(attachment_id)
        if (
            attachment is None
            or (
                attachment.article_id is not None
                and attachment.article_id != article.id
            )
            or (
                attachment.article_id is None
                and attachment.uploaded_by_id != current.id
                and not privileged
            )
        ):
            # Do not disclose whether an ID belongs to another workspace/user or
            # is already attached to a private article.
            raise HTTPException(
                status_code=422,
                detail="Один из файлов недоступен или уже используется в другой статье",
            )

    removed_storage_paths: list[str] = []
    for attachment in rows:
        if attachment.id in wanted:
            attachment.article_id = article.id
        elif attachment.article_id == article.id:
            removed_storage_paths.append(attachment.storage_path)
            await db.delete(attachment)
    return removed_storage_paths




def _required_title(value: object, entity: str) -> str:
    title = str(value or "").strip()
    if not title:
        raise HTTPException(
            status_code=422,
            detail=f"Название {entity} не может состоять только из пробелов",
        )
    return title


async def _user_names(db: AsyncSession, workspace_id: uuid.UUID) -> dict[uuid.UUID, str]:
    rows = await db.execute(
        select(User.id, User.name).where(User.workspace_id == workspace_id)
    )
    return {row.id: row.name for row in rows}


def _article_rights(
    article: KnowledgeArticle, rights: dict[uuid.UUID, dict], admin: bool
) -> dict:
    if admin:
        return dict(FULL_ACCESS)
    if article.section_id is None:
        return dict(DEFAULT_ACCESS)
    return rights.get(article.section_id, DEFAULT_ACCESS)


def _can_see_article(
    article: KnowledgeArticle,
    rights: dict[uuid.UUID, dict],
    current: User,
    admin: bool,
) -> bool:
    """Виден ли читателю сам факт существования статьи.

    Сначала права на раздел: закрытый раздел не должен просвечивать ни в дереве,
    ни в поиске. Затем статус — черновик и архив видны только автору и тем, кто
    может править раздел: черновик для того и черновик, чтобы его пока не читали.
    """
    access = _article_rights(article, rights, admin)
    if not access["can_view"]:
        return False
    if article.status == ArticleStatus.published:
        return True
    if admin or article.created_by_id == current.id:
        return True
    return access["can_edit"]


def _section_row(section: KnowledgeSection, rights: dict) -> dict:
    return {
        "id": str(section.id),
        "parent_id": str(section.parent_id) if section.parent_id else None,
        "title": section.title,
        "icon": section.icon,
        "position": section.position,
        "rights": rights,
        "articles": [],
        "children": [],
    }


def _article_stub(article: KnowledgeArticle) -> dict:
    return {
        "id": str(article.id),
        "section_id": str(article.section_id) if article.section_id else None,
        "parent_id": str(article.parent_id) if article.parent_id else None,
        "title": article.title,
        "status": article.status.value,
        "position": article.position,
        "updated_at": article.updated_at,
        "published_at": article.published_at,
    }


def _excerpt(article: KnowledgeArticle, query: str) -> str:
    """Кусок текста вокруг найденного слова — чтобы было видно, почему нашлось."""
    text = " ".join((article.search_text or "").split())
    index = text.lower().find(query.lower())
    if index < 0:
        return text[:180]
    start = max(0, index - 70)
    piece = text[start : start + 200]
    return ("…" if start else "") + piece + ("…" if start + 200 < len(text) else "")
