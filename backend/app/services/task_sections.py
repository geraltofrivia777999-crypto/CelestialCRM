"""Права на разделы доски задач — ТЗ 8.1.

Разделы делят доску по отделам: у дизайнеров свои задачи, у баеров свои. Права
устроены как в базе знаний, но с другим умолчанием.

Раздел без единого правила открыт: его видно всем с `workspace.view`, и в нём
можно заводить задачи. Иначе появление разделов отняло бы у команды доску,
которой она пользовалась до сих пор.

Правка и удаление чужих карточек — отдельные права, и по умолчанию их нет:
своя задача и задача, где ты исполнитель, и так редактируются по правилу
карточки. Право в разделе это правило расширяет, а не заменяет.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TaskSection, TaskSectionAccess, User

# Раздел, про который никто ничего не сказал, ведёт себя как доска до разделов.
DEFAULT_ACCESS = {
    "can_view": True,
    "can_create": True,
    "can_edit": False,
    "can_delete": False,
    "can_manage": False,
}
FULL_ACCESS = {key: True for key in DEFAULT_ACCESS}
CLOSED_ACCESS = {key: False for key in DEFAULT_ACCESS}


async def sections(db: AsyncSession, workspace_id: uuid.UUID) -> list[TaskSection]:
    return list(
        (
            await db.execute(
                select(TaskSection)
                .where(TaskSection.workspace_id == workspace_id)
                .order_by(TaskSection.position, TaskSection.created_at)
            )
        ).scalars()
    )


async def section_rights(
    db: AsyncSession, user: User, *, is_admin: bool
) -> dict[uuid.UUID, dict]:
    """Права пользователя на каждый раздел доски.

    Именное правило сильнее ролевого: «этот раздел видит только Ирина»
    описывается одним правилом на человека.

    Если на разделе есть правила, но ни одно не про этого человека, раздел для
    него закрыт — именно так одно именное правило закрывает раздел для всех
    остальных.
    """
    rows = await sections(db, user.workspace_id)
    if not rows:
        return {}
    if is_admin:
        return {section.id: dict(FULL_ACCESS) for section in rows}

    rules = {
        rule.section_id: rule
        for rule in (
            await db.execute(
                select(TaskSectionAccess).where(
                    TaskSectionAccess.workspace_id == user.workspace_id,
                    TaskSectionAccess.role_id == user.role_id,
                    TaskSectionAccess.user_id.is_(None),
                )
            )
        ).scalars()
    }
    rules.update(
        {
            rule.section_id: rule
            for rule in (
                await db.execute(
                    select(TaskSectionAccess).where(
                        TaskSectionAccess.workspace_id == user.workspace_id,
                        TaskSectionAccess.user_id == user.id,
                    )
                )
            ).scalars()
        }
    )
    guarded = set(
        (
            await db.execute(
                select(TaskSectionAccess.section_id).where(
                    TaskSectionAccess.workspace_id == user.workspace_id
                )
            )
        ).scalars()
    )

    rights: dict[uuid.UUID, dict] = {}
    for section in rows:
        rule = rules.get(section.id)
        if rule is not None:
            rights[section.id] = {
                "can_view": rule.can_view,
                "can_create": rule.can_create,
                "can_edit": rule.can_edit,
                "can_delete": rule.can_delete,
                "can_manage": rule.can_manage,
            }
        elif section.id in guarded:
            rights[section.id] = dict(CLOSED_ACCESS)
        else:
            rights[section.id] = dict(DEFAULT_ACCESS)
    return rights
