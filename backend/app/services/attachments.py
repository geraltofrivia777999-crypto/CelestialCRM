"""Приём файлов воркспейса: статьи базы знаний (ТЗ 8.2) и поля задач (ТЗ 8.1).

Загрузка одинакова для обоих разделов — читаем не больше лимита, проверяем тип,
считаем квоту под блокировкой воркспейса и кладём байты на диск. Отличаются
только права на вызов и то, к чему файл потом привяжется, поэтому здесь нет ни
одной проверки прав: их делает роутер, который знает свой раздел.

Функция намеренно не коммитит. Запись в базе и байты на диске должны появиться
или исчезнуть вместе, а решение об этом принимает вызывающий код — он же пишет
запись в аудит в той же транзакции.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import KnowledgeAttachment, User, Workspace
from app.services import storage


async def delete_stale_unbound(
    db: AsyncSession, workspace_id: uuid.UUID
) -> list[KnowledgeAttachment]:
    """Пометить к удалению загрузки, которые так и не закрепили за объектом.

    Незакреплённое вложение — это файл, чей редактор закрыли не сохранив. Через
    сутки такой файл уже точно никому не нужен, но место в квоте занимает.
    Вложение задачи узнаётся по `task_id`, статьи — по `article_id`, запись
    интервью — по `candidate_id`; пустые все три только у брошенных.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=settings.upload_unbound_ttl_hours)
    rows = list(
        (
            await db.execute(
                select(KnowledgeAttachment)
                .where(
                    KnowledgeAttachment.workspace_id == workspace_id,
                    KnowledgeAttachment.article_id.is_(None),
                    KnowledgeAttachment.task_id.is_(None),
                    KnowledgeAttachment.candidate_id.is_(None),
                    KnowledgeAttachment.created_at < cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    for attachment in rows:
        await db.delete(attachment)
    return rows


async def receive_upload(
    db: AsyncSession, current: User, file: UploadFile
) -> tuple[KnowledgeAttachment, list[KnowledgeAttachment]]:
    """Принять файл и добавить запись в сессию, не коммитя её.

    Возвращает новое вложение и брошенные записи: их байты можно стирать с
    диска только после того, как транзакция прошла.
    """
    # Читаем не больше лимита плюс байт: поддельный multipart иначе заставит нас
    # держать в памяти тело любого размера ещё до того, как мы его отвергнем.
    try:
        content = await file.read(settings.upload_max_bytes + 1)
    finally:
        await file.close()
    mime_type = storage.normalize_mime_type(
        file.content_type or "application/octet-stream", content
    )

    # Блокировка воркспейса выстраивает проверки квоты в очередь: без неё две
    # одновременные загрузки увидят одно и то же свободное место.
    await db.scalar(
        select(Workspace.id)
        .where(Workspace.id == current.workspace_id)
        .with_for_update()
    )
    stale = await delete_stale_unbound(db, current.workspace_id)
    used_bytes = await db.scalar(
        select(func.coalesce(func.sum(KnowledgeAttachment.byte_size), 0)).where(
            KnowledgeAttachment.workspace_id == current.workspace_id
        )
    )
    if int(used_bytes or 0) + len(content) > settings.upload_workspace_quota_bytes:
        quota_mb = settings.upload_workspace_quota_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"Квота файлов воркспейса ({quota_mb} МБ) исчерпана",
        )

    try:
        path = storage.store(
            current.workspace_id,
            file.filename or "file",
            mime_type,
            content,
        )
    except storage.StorageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    attachment = KnowledgeAttachment(
        workspace_id=current.workspace_id,
        file_name=storage.safe_file_name(file.filename or "file"),
        mime_type=mime_type,
        byte_size=len(content),
        storage_path=path,
        uploaded_by_id=current.id,
    )
    db.add(attachment)
    return attachment, stale


def row(attachment: KnowledgeAttachment, url_prefix: str) -> dict:
    return {
        "id": str(attachment.id),
        "url": f"{url_prefix}/{attachment.id}",
        "file_name": attachment.file_name,
        "mime_type": attachment.mime_type,
        "byte_size": attachment.byte_size,
        "kind": storage.kind_for(attachment.mime_type),
    }
